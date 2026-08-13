"""
Source Registry and Policy Gates System
Implements source registration, policy decisions, and legal compliance per Festival Bloomberg spec
"""
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, field
import re
import httpx

logger = logging.getLogger(__name__)


class SourceType(Enum):
    """Source system types"""
    OFFICIAL_API = "official_api"
    TICKETING = "ticketing"
    STREAMING = "streaming"
    SOCIAL = "social"
    CRM = "crm"
    CONTRACT = "contract"
    PUBLIC_WEB = "public_web"
    ANALYST_INPUT = "analyst_input"
    FILE_UPLOAD = "file_upload"


class PolicyDecision(Enum):
    """Policy decision outcomes"""
    ALLOWED = "allowed"
    DENIED = "denied"
    CONDITIONAL = "conditional"
    REVIEW_REQUIRED = "review_required"


class PolicyReason(Enum):
    """Policy decision reasons"""
    TERMS_ACCEPTABLE = "terms_acceptable"
    ROBOTS_TXT_ALLOWED = "robots_txt_allowed"
    PUBLICLY_AVAILABLE = "publicly_available"
    TERMS_PROHIBITED = "terms_prohibited"
    ROBOTS_TXT_DENIED = "robots_txt_denied"
    ACCESS_CONTROLLED = "access_controlled"
    AUTHENTICATION_REQUIRED = "authentication_required"
    PAYWALLED = "paywalled"
    LEGAL_RISK = "legal_risk"
    PRIVACY_RISK = "privacy_risk"
    BUDGET_EXCEEDED = "budget_exceeded"
    RATE_LIMITED = "rate_limited"
    UNKNOWN_SOURCE = "unknown_source"


@dataclass
class PolicyGateResult:
    """Policy gate decision result"""
    decision: PolicyDecision
    reason: PolicyReason
    confidence: float  # 0-1
    conditions: Optional[List[str]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceRegistration:
    """Source system registration"""
    source_id: str
    provider_name: str
    source_type: SourceType
    base_url: str
    api_version: Optional[str] = None
    terms_url: Optional[str] = None
    robots_txt_url: Optional[str] = None
    refresh_cadence: Optional[str] = None
    active: bool = True
    registered_at: datetime = field(default_factory=datetime.utcnow)
    last_reviewed_at: Optional[datetime] = None
    legal_review_status: str = "pending"  # pending, approved, rejected
    legal_review_notes: Optional[str] = None
    budget_class: str = "free_http"
    rate_limit_per_minute: int = 60
    requires_authentication: bool = False
    authentication_type: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class SourceRegistry:
    """
    Source registry for managing data sources with policy gates
    Implements Festival Bloomberg source registry requirements
    """
    
    def __init__(self):
        self._sources: Dict[str, SourceRegistration] = {}
        self._policy_decisions: Dict[str, PolicyGateResult] = {}
        self._robots_txt_cache: Dict[str, str] = {}
        self._terms_cache: Dict[str, str] = {}
        
        # Initialize with known sources
        self._initialize_known_sources()
        
        logger.info("Source registry initialized")
    
    def _initialize_known_sources(self):
        """Initialize with known music/festival data sources"""
        known_sources = [
            SourceRegistration(
                source_id="musicbrainz",
                provider_name="MusicBrainz",
                source_type=SourceType.OFFICIAL_API,
                base_url="https://musicbrainz.org/ws/2/",
                api_version="2",
                terms_url="https://musicbrainz.org/doc/XML_Web_Service/Rate_Limit",
                robots_txt_url="https://musicbrainz.org/robots.txt",
                refresh_cadence="daily",
                budget_class="free_http",
                rate_limit_per_minute=60,
                legal_review_status="approved",
                legal_review_notes="CC0 license, public API"
            ),
            SourceRegistration(
                source_id="lastfm",
                provider_name="Last.fm",
                source_type=SourceType.OFFICIAL_API,
                base_url="https://ws.audioscrobbler.com/2.0/",
                api_version="2.0",
                terms_url="https://www.last.fm/api/tos",
                robots_txt_url="https://www.last.fm/robots.txt",
                refresh_cadence="daily",
                budget_class="free_http",
                rate_limit_per_minute=60,
                legal_review_status="approved",
                legal_review_notes="API terms available, public access"
            ),
            SourceRegistration(
                source_id="spotify",
                provider_name="Spotify",
                source_type=SourceType.STREAMING,
                base_url="https://api.spotify.com/v1/",
                api_version="v1",
                terms_url="https://developer.spotify.com/terms/",
                robots_txt_url="https://www.spotify.com/robots.txt",
                refresh_cadence="daily",
                budget_class="free_http",
                rate_limit_per_minute=60,
                requires_authentication=True,
                authentication_type="oauth2",
                legal_review_status="approved",
                legal_review_notes="Developer API with authentication"
            ),
            SourceRegistration(
                source_id="ticketmaster",
                provider_name="Ticketmaster",
                source_type=SourceType.TICKETING,
                base_url="https://app.ticketmaster.com/",
                api_version="v2",
                terms_url="https://developer.ticketmaster.com/products-and-docs/apis/",
                robots_txt_url="https://www.ticketmaster.com/robots.txt",
                refresh_cadence="daily",
                budget_class="free_http",
                rate_limit_per_minute=60,
                requires_authentication=True,
                authentication_type="api_key",
                legal_review_status="approved",
                legal_review_notes="Official API for ticketing data"
            ),
            SourceRegistration(
                source_id="songkick",
                provider_name="Songkick",
                source_type=SourceType.OFFICIAL_API,
                base_url="https://api.songkick.com/api/3.0/",
                api_version="3.0",
                terms_url="https://www.songkick.com/developer/terms",
                robots_txt_url="https://www.songkick.com/robots.txt",
                refresh_cadence="daily",
                budget_class="free_http",
                rate_limit_per_minute=60,
                requires_authentication=True,
                authentication_type="api_key",
                legal_review_status="approved",
                legal_review_notes="Concert discovery API"
            )
        ]
        
        for source in known_sources:
            self._sources[source.source_id] = source
            logger.info(f"Registered source: {source.source_id}")
    
    def register_source(self, registration: SourceRegistration) -> str:
        """
        Register a new source system
        
        Args:
            registration: Source registration details
            
        Returns:
            Source ID
        """
        self._sources[registration.source_id] = registration
        logger.info(f"Registered new source: {registration.source_id}")
        return registration.source_id
    
    def get_source(self, source_id: str) -> Optional[SourceRegistration]:
        """Get source registration by ID"""
        return self._sources.get(source_id)
    
    def list_sources(self, 
                    source_type: Optional[SourceType] = None,
                    active_only: bool = True) -> List[SourceRegistration]:
        """
        List registered sources
        
        Args:
            source_type: Filter by source type
            active_only: Only return active sources
            
        Returns:
            List of source registrations
        """
        sources = list(self._sources.values())
        
        if source_type:
            sources = [s for s in sources if s.source_type == source_type]
        
        if active_only:
            sources = [s for s in sources if s.rate_limit_per_minute > 0]
        
        return sources
    
    def _fetch_robots_txt(self, url: str) -> Optional[str]:
        """Fetch robots.txt for a domain"""
        try:
            robots_url = f"{url.rstrip('/')}/robots.txt"
            response = httpx.get(robots_url, timeout=10)
            
            if response.status_code == 200:
                content = response.text
                self._robots_txt_cache[url] = content
                return content
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to fetch robots.txt for {url}: {e}")
            return None
    
    def _check_robots_txt_allowed(self, user_agent: str, path: str, robots_txt: str) -> bool:
        """
        Check if robots.txt allows access
        
        Args:
            user_agent: User agent string
            path: Path to check
            robots_txt: Robots.txt content
            
        Returns:
            True if allowed, False otherwise
        """
        # Simple robots.txt parser (should use proper library in production)
        lines = robots_txt.split('\n')
        
        current_user_agent = None
        disallowed_paths = []
        
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            if line.lower().startswith('user-agent:'):
                current_user_agent = line.split(':', 1)[1].strip()
            elif line.lower().startswith('disallow:'):
                if current_user_agent == '*' or current_user_agent == user_agent:
                    disallowed_path = line.split(':', 1)[1].strip()
                    disallowed_paths.append(disallowed_path)
        
        # Check if path is disallowed
        for disallowed in disallowed_paths:
            if path.startswith(disallowed):
                return False
        
        return True
    
    def _check_terms_acceptable(self, terms_url: str) -> PolicyGateResult:
        """
        Check if terms of service are acceptable
        
        Args:
            terms_url: URL to terms of service
            
        Returns:
            Policy gate result
        """
        # In production, this would use LLM to analyze terms
        # For now, return conditional result requiring review
        
        return PolicyGateResult(
            decision=PolicyDecision.REVIEW_REQUIRED,
            reason=PolicyReason.TERMS_ACCEPTABLE,
            confidence=0.5,
            conditions=["Manual legal review required"],
            metadata={"terms_url": terms_url}
        )
    
    def evaluate_policy_gate(self, 
                            source_id: str,
                            url: str,
                            user_agent: str = "*") -> PolicyGateResult:
        """
        Evaluate policy gate for a source and URL
        
        Args:
            source_id: Source system ID
            url: Target URL
            user_agent: User agent string
            
        Returns:
            Policy gate result
        """
        source = self.get_source(source_id)
        
        if not source:
            return PolicyGateResult(
                decision=PolicyDecision.DENIED,
                reason=PolicyReason.UNKNOWN_SOURCE,
                confidence=1.0,
                metadata={"source_id": source_id}
            )
        
        if not source.active:
            return PolicyGateResult(
                decision=PolicyDecision.DENIED,
                reason=PolicyReason.TERMS_PROHIBITED,
                confidence=1.0,
                metadata={"source_id": source_id, "reason": "Source inactive"}
            )
        
        # Check legal review status
        if source.legal_review_status != "approved":
            return PolicyGateResult(
                decision=PolicyDecision.DENIED,
                reason=PolicyReason.LEGAL_RISK,
                confidence=1.0,
                metadata={"source_id": source_id, "legal_status": source.legal_review_status}
            )
        
        # Check robots.txt if available
        if source.robots_txt_url:
            robots_txt = self._robots_txt_cache.get(source.robots_txt_url)
            if not robots_txt:
                robots_txt = self._fetch_robots_txt(source.base_url)
            
            if robots_txt:
                path = '/' + '/'.join(url.split('/')[3:])  # Extract path
                if not self._check_robots_txt_allowed(user_agent, path, robots_txt):
                    return PolicyGateResult(
                        decision=PolicyDecision.DENIED,
                        reason=PolicyReason.ROBOTS_TXT_DENIED,
                        confidence=0.9,
                        metadata={"source_id": source_id, "robots_txt": "disallowed"}
                    )
        
        # Check if source requires authentication
        if source.requires_authentication:
            return PolicyGateResult(
                decision=PolicyDecision.CONDITIONAL,
                reason=PolicyReason.AUTHENTICATION_REQUIRED,
                confidence=1.0,
                conditions=[f"Requires {source.authentication_type} authentication"],
                metadata={"source_id": source_id, "auth_type": source.authentication_type}
            )
        
        # Source is allowed
        return PolicyGateResult(
            decision=PolicyDecision.ALLOWED,
            reason=PolicyReason.TERMS_ACCEPTABLE,
            confidence=0.95,
            metadata={"source_id": source_id, "budget_class": source.budget_class}
        )
    
    def update_source_status(self, 
                          source_id: str,
                          active: bool,
                          legal_review_status: Optional[str] = None,
                          legal_review_notes: Optional[str] = None):
        """
        Update source status
        
        Args:
            source_id: Source ID
            active: Whether source is active
            legal_review_status: Legal review status
            legal_review_notes: Legal review notes
        """
        source = self.get_source(source_id)
        if source:
            source.active = active
            if legal_review_status:
                source.legal_review_status = legal_review_status
            if legal_review_notes:
                source.legal_review_notes = legal_review_notes
            source.last_reviewed_at = datetime.utcnow()
            logger.info(f"Updated source status: {source_id}")
    
    def get_rate_limit(self, source_id: str) -> int:
        """Get rate limit for a source"""
        source = self.get_source(source_id)
        return source.rate_limit_per_minute if source else 0
    
    def get_budget_class(self, source_id: str) -> str:
        """Get budget class for a source"""
        source = self.get_source(source_id)
        return source.budget_class if source else "free_http"
    
    def check_rate_limit(self, source_id: str, current_requests: int) -> bool:
        """
        Check if rate limit allows request
        
        Args:
            source_id: Source ID
            current_requests: Current request count
            
        Returns:
            True if allowed, False otherwise
        """
        limit = self.get_rate_limit(source_id)
        return current_requests < limit


class PolicyGateEngine:
    """
    Policy gate engine for enforcing acquisition policies
    Implements Festival Bloomberg policy gate requirements
    """
    
    def __init__(self, source_registry: SourceRegistry):
        self.source_registry = source_registry
        self._decision_cache: Dict[str, PolicyGateResult] = {}
        
        # Prohibited patterns
        self._prohibited_patterns = [
            r'/login',
            r'/admin',
            r'/private',
            r'/secure',
            r'/auth',
            r'/account',
            r'/checkout',
            r'/payment',
            r'/subscription'
        ]
        
        logger.info("Policy gate engine initialized")
    
    def _check_prohibited_patterns(self, url: str) -> bool:
        """Check if URL contains prohibited patterns"""
        for pattern in self._prohibited_patterns:
            if re.search(pattern, url, re.IGNORECASE):
                return True
        return False
    
    def evaluate_acquisition(self,
                           source_id: str,
                           url: str,
                           user_agent: str = "FestivalBloomberg/1.0") -> PolicyGateResult:
        """
        Evaluate acquisition request against all policies
        
        Args:
            source_id: Source system ID
            url: Target URL
            user_agent: User agent string
            
        Returns:
            Policy gate result
        """
        cache_key = f"{source_id}:{url}"
        
        # Check cache
        if cache_key in self._decision_cache:
            return self._decision_cache[cache_key]
        
        # Check prohibited patterns
        if self._check_prohibited_patterns(url):
            result = PolicyGateResult(
                decision=PolicyDecision.DENIED,
                reason=PolicyReason.ACCESS_CONTROLLED,
                confidence=1.0,
                metadata={"url": url, "reason": "Prohibited pattern"}
            )
            self._decision_cache[cache_key] = result
            return result
        
        # Evaluate source registry policy
        result = self.source_registry.evaluate_policy_gate(source_id, url, user_agent)
        
        # Cache result
        self._decision_cache[cache_key] = result
        
        return result
    
    def clear_cache(self):
        """Clear decision cache"""
        self._decision_cache.clear()
        logger.info("Policy gate cache cleared")
    
    def get_decision_stats(self) -> Dict[str, Any]:
        """Get decision statistics"""
        decisions = list(self._decision_cache.values())
        
        return {
            "total_decisions": len(decisions),
            "allowed": sum(1 for d in decisions if d.decision == PolicyDecision.ALLOWED),
            "denied": sum(1 for d in decisions if d.decision == PolicyDecision.DENIED),
            "conditional": sum(1 for d in decisions if d.decision == PolicyDecision.CONDITIONAL),
            "review_required": sum(1 for d in decisions if d.decision == PolicyDecision.REVIEW_REQUIRED)
        }


def create_source_registry() -> SourceRegistry:
    """Factory function to create source registry"""
    return SourceRegistry()


def create_policy_gate_engine(source_registry: SourceRegistry) -> PolicyGateEngine:
    """Factory function to create policy gate engine"""
    return PolicyGateEngine(source_registry)
