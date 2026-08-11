"""
Tiered Scraping Architecture
Implements cost-optimized tiered scraping per Festival Bloomberg spec
Tier order: HTTP → Playwright → Monid → Apify
"""
import logging
import hashlib
from typing import Optional, Dict, Any, List
from datetime import datetime
from enum import Enum
from dataclasses import dataclass
import httpx
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, Browser, Page
import selectolax

logger = logging.getLogger(__name__)


class BudgetClass(Enum):
    """Budget classes for cost optimization"""
    FREE_HTTP = "free_http"
    CHEAP_BROWSER = "cheap_browser"
    MANAGED_STANDARD = "managed_standard"
    MANAGED_SCALE = "managed_scale"
    MANUAL_REVIEW = "manual_review"


class AcquisitionStatus(Enum):
    """Acquisition status codes"""
    SUCCESS = "success"
    FALLBACK_REQUIRED = "fallback_required"
    POLICY_DENIED = "policy_denied"
    ACCESS_RESTRICTED = "access_restricted"
    TRANSIENT_NETWORK = "transient_network"
    SOURCE_CHANGED = "source_changed"
    PARSE_FAILED = "parse_failed"
    SCHEMA_INVALID = "schema_invalid"
    PROVIDER_ERROR = "provider_error"
    BUDGET_EXCEEDED = "budget_exceeded"


@dataclass
class AcquisitionJob:
    """Acquisition job definition"""
    job_id: str
    source_id: str
    url: str
    canonical_url: str
    method: str = "http"
    headers: Optional[Dict[str, str]] = None
    timeout: int = 30
    rendering_required: bool = False
    max_bytes: int = 10_000_000
    policy_decision_id: Optional[str] = None
    budget_class: BudgetClass = BudgetClass.FREE_HTTP
    correlation_id: Optional[str] = None


@dataclass
class AcquisitionResult:
    """Acquisition result"""
    job_id: str
    status: AcquisitionStatus
    final_url: str
    retrieval_timestamp: datetime
    content_type: Optional[str]
    content_length: int
    content_hash: str
    normalized_text: Optional[str]
    normalized_html: Optional[str]
    http_status: Optional[int]
    renderer_metadata: Optional[Dict[str, Any]]
    error_category: Optional[str]
    provider_cost_estimate: float
    provenance: Dict[str, Any]
    tier_used: str


class TieredScraper:
    """
    Tiered scraping engine with cost optimization
    Implements Festival Bloomberg tiered architecture
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self._http_client = None
        self._playwright_browser = None
        self._cache = {}  # Simple in-memory cache (should be replaced with Redis in production)
        
        # Cost tracking
        self._cost_tracker = {
            'http_calls': 0,
            'browser_calls': 0,
            'monid_calls': 0,
            'apify_calls': 0,
            'total_cost': 0.0
        }
        
        # Rate limiting
        self._domain_limits = {}
        self._request_history = {}
        
        logger.info("Tiered scraper initialized")
    
    def _get_http_client(self) -> httpx.Client:
        """Get or create HTTP client"""
        if self._http_client is None:
            timeout = httpx.Timeout(self.config.get('http_timeout', 30.0))
            self._http_client = httpx.Client(timeout=timeout, follow_redirects=True)
        return self._http_client
    
    def _get_playwright_browser(self) -> Browser:
        """Get or create Playwright browser"""
        if self._playwright_browser is None:
            playwright = sync_playwright().start()
            self._playwright_browser = playwright.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox']
            )
        return self._playwright_browser
    
    def _generate_content_hash(self, content: bytes) -> str:
        """Generate SHA256 hash of content"""
        return hashlib.sha256(content).hexdigest()
    
    def _canonicalize_url(self, url: str) -> str:
        """Canonicalize URL for caching"""
        # Basic canonicalization - should be enhanced
        url = url.lower()
        url = url.replace('https://', 'https://')
        url = url.replace('http://', 'http://')
        # Remove tracking parameters
        if '?' in url:
            base, params = url.split('?', 1)
            # Keep only essential params
            essential_params = []
            for param in params.split('&'):
                if any(essential in param for essential in ['id=', 'page=', 'v=']):
                    essential_params.append(param)
            if essential_params:
                url = f"{base}?{'&'.join(essential_params)}"
            else:
                url = base
        return url
    
    def _check_cache(self, canonical_url: str) -> Optional[AcquisitionResult]:
        """Check cache for existing result"""
        cache_key = canonical_url
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            # Check if cache is still fresh (24h default)
            age = (datetime.utcnow() - cached.retrieval_timestamp).total_seconds()
            if age < 86400:  # 24 hours
                logger.debug(f"Cache hit for {canonical_url}")
                return cached
        return None
    
    def _update_cache(self, result: AcquisitionResult):
        """Update cache with new result"""
        cache_key = result.final_url
        self._cache[cache_key] = result
    
    def _check_rate_limit(self, domain: str) -> bool:
        """Check if domain rate limit allows request"""
        if domain not in self._domain_limits:
            return True
        
        limit = self._domain_limits[domain]
        now = datetime.utcnow()
        
        # Clean old requests
        self._request_history[domain] = [
            ts for ts in self._request_history.get(domain, [])
            if (now - ts).total_seconds() < 60  # 1 minute window
        ]
        
        return len(self._request_history[domain]) < limit
    
    def _record_request(self, domain: str):
        """Record request for rate limiting"""
        if domain not in self._request_history:
            self._request_history[domain] = []
        self._request_history[domain].append(datetime.utcnow())
    
    def _tier_1_http_fetch(self, job: AcquisitionJob) -> AcquisitionResult:
        """
        Tier 1: HTTP fetch with lightweight parsing
        Fast, cheap, suitable for static content
        """
        try:
            client = self._get_http_client()
            
            # Check rate limit
            domain = job.url.split('/')[2]
            if not self._check_rate_limit(domain):
                return AcquisitionResult(
                    job_id=job.job_id,
                    status=AcquisitionStatus.TRANSIENT_NETWORK,
                    final_url=job.url,
                    retrieval_timestamp=datetime.utcnow(),
                    content_type=None,
                    content_length=0,
                    content_hash="",
                    normalized_text=None,
                    normalized_html=None,
                    http_status=429,
                    renderer_metadata=None,
                    error_category="rate_limit",
                    provider_cost_estimate=0.001,
                    provenance={'tier': 'http', 'method': 'GET'},
                    tier_used="http"
                )
            
            # Make request
            headers = job.headers or {
                'User-Agent': 'Mozilla/5.0 (compatible; FestivalBloomberg/1.0)'
            }
            
            response = client.get(job.url, headers=headers, timeout=job.timeout)
            
            self._record_request(domain)
            self._cost_tracker['http_calls'] += 1
            self._cost_tracker['total_cost'] += 0.001
            
            # Check response
            if response.status_code == 200:
                content = response.content
                content_hash = self._generate_content_hash(content)
                
                # Parse with selectolax (faster than BeautifulSoup)
                tree = selectolax.HTMLParser(content)
                normalized_text = tree.body.text() if tree.body else ""
                normalized_html = content.decode('utf-8', errors='ignore')
                
                # Validate content is not empty/blocked
                if len(normalized_text) < 100:
                    return AcquisitionResult(
                        job_id=job.job_id,
                        status=AcquisitionStatus.FALLBACK_REQUIRED,
                        final_url=str(response.url),
                        retrieval_timestamp=datetime.utcnow(),
                        content_type=response.headers.get('content-type'),
                        content_length=len(content),
                        content_hash=content_hash,
                        normalized_text=normalized_text,
                        normalized_html=normalized_html,
                        http_status=response.status_code,
                        renderer_metadata=None,
                        error_category="insufficient_content",
                        provider_cost_estimate=0.001,
                        provenance={'tier': 'http', 'method': 'GET'},
                        tier_used="http"
                    )
                
                return AcquisitionResult(
                    job_id=job.job_id,
                    status=AcquisitionStatus.SUCCESS,
                    final_url=str(response.url),
                    retrieval_timestamp=datetime.utcnow(),
                    content_type=response.headers.get('content-type'),
                    content_length=len(content),
                    content_hash=content_hash,
                    normalized_text=normalized_text,
                    normalized_html=normalized_html,
                    http_status=response.status_code,
                    renderer_metadata=None,
                    error_category=None,
                    provider_cost_estimate=0.001,
                    provenance={'tier': 'http', 'method': 'GET'},
                    tier_used="http"
                )
            
            elif response.status_code in [403, 401, 429]:
                return AcquisitionResult(
                    job_id=job.job_id,
                    status=AcquisitionStatus.ACCESS_RESTRICTED,
                    final_url=str(response.url),
                    retrieval_timestamp=datetime.utcnow(),
                    content_type=response.headers.get('content-type'),
                    content_length=0,
                    content_hash="",
                    normalized_text=None,
                    normalized_html=None,
                    http_status=response.status_code,
                    renderer_metadata=None,
                    error_category="access_denied",
                    provider_cost_estimate=0.001,
                    provenance={'tier': 'http', 'method': 'GET'},
                    tier_used="http"
                )
            
            else:
                return AcquisitionResult(
                    job_id=job.job_id,
                    status=AcquisitionStatus.FALLBACK_REQUIRED,
                    final_url=str(response.url),
                    retrieval_timestamp=datetime.utcnow(),
                    content_type=response.headers.get('content-type'),
                    content_length=0,
                    content_hash="",
                    normalized_text=None,
                    normalized_html=None,
                    http_status=response.status_code,
                    renderer_metadata=None,
                    error_category="unexpected_status",
                    provider_cost_estimate=0.001,
                    provenance={'tier': 'http', 'method': 'GET'},
                    tier_used="http"
                )
                
        except httpx.TimeoutException:
            return AcquisitionResult(
                job_id=job.job_id,
                status=AcquisitionStatus.TRANSIENT_NETWORK,
                final_url=job.url,
                retrieval_timestamp=datetime.utcnow(),
                content_type=None,
                content_length=0,
                content_hash="",
                normalized_text=None,
                normalized_html=None,
                http_status=None,
                renderer_metadata=None,
                error_category="timeout",
                provider_cost_estimate=0.001,
                provenance={'tier': 'http', 'method': 'GET'},
                tier_used="http"
            )
        except Exception as e:
            logger.error(f"HTTP fetch failed: {e}")
            return AcquisitionResult(
                job_id=job.job_id,
                status=AcquisitionStatus.PROVIDER_ERROR,
                final_url=job.url,
                retrieval_timestamp=datetime.utcnow(),
                content_type=None,
                content_length=0,
                content_hash="",
                normalized_text=None,
                normalized_html=None,
                http_status=None,
                renderer_metadata=None,
                error_category=str(e),
                provider_cost_estimate=0.001,
                provenance={'tier': 'http', 'method': 'GET'},
                tier_used="http"
            )
    
    def _tier_2_playwright_fetch(self, job: AcquisitionJob) -> AcquisitionResult:
        """
        Tier 2: Playwright browser automation
        Handles JavaScript-rendered content
        """
        try:
            browser = self._get_playwright_browser()
            
            # Create context with resource blocking
            context = browser.new_context(
                user_agent='Mozilla/5.0 (compatible; FestivalBloomberg/1.0)',
                viewport={'width': 1920, 'height': 1080}
            )
            
            # Block heavy resources to save bandwidth
            def block_heavy_assets(route):
                resource_type = route.request.resource_type
                if resource_type in ['image', 'media', 'font', 'stylesheet']:
                    return route.abort()
                return route.continue_()
            
            page = context.new_page()
            page.route("**/*", block_heavy_assets)
            
            # Navigate to URL
            response = page.goto(job.url, wait_until='domcontentloaded', timeout=job.timeout * 1000)
            
            # Wait for content to load
            page.wait_for_timeout(2000)  # 2 seconds for dynamic content
            
            # Get content
            content = page.content()
            content_hash = self._generate_content_hash(content.encode('utf-8'))
            
            # Parse content
            tree = selectolax.HTMLParser(content.encode('utf-8'))
            normalized_text = tree.body.text() if tree.body else ""
            
            # Cleanup
            context.close()
            
            self._cost_tracker['browser_calls'] += 1
            self._cost_tracker['total_cost'] += 0.01
            
            return AcquisitionResult(
                job_id=job.job_id,
                status=AcquisitionStatus.SUCCESS,
                final_url=page.url,
                retrieval_timestamp=datetime.utcnow(),
                content_type='text/html',
                content_length=len(content),
                content_hash=content_hash,
                normalized_text=normalized_text,
                normalized_html=content,
                http_status=response.status,
                renderer_metadata={'browser': 'chromium', 'headless': True},
                error_category=None,
                provider_cost_estimate=0.01,
                provenance={'tier': 'playwright', 'method': 'browser'},
                tier_used="playwright"
            )
            
        except Exception as e:
            logger.error(f"Playwright fetch failed: {e}")
            return AcquisitionResult(
                job_id=job.job_id,
                status=AcquisitionStatus.PROVIDER_ERROR,
                final_url=job.url,
                retrieval_timestamp=datetime.utcnow(),
                content_type=None,
                content_length=0,
                content_hash="",
                normalized_text=None,
                normalized_html=None,
                http_status=None,
                renderer_metadata=None,
                error_category=str(e),
                provider_cost_estimate=0.01,
                provenance={'tier': 'playwright', 'method': 'browser'},
                tier_used="playwright"
            )
    
    def _tier_3_monid_fetch(self, job: AcquisitionJob) -> AcquisitionResult:
        """
        Tier 3: Monid integration (placeholder)
        Would integrate with monid-prod for managed retrieval
        """
        # Placeholder for Monid integration
        # This would call the Monid API through the adapter pattern
        
        logger.warning(f"Monid integration not yet implemented for job {job.job_id}")
        
        return AcquisitionResult(
            job_id=job.job_id,
            status=AcquisitionStatus.PROVIDER_ERROR,
            final_url=job.url,
            retrieval_timestamp=datetime.utcnow(),
            content_type=None,
            content_length=0,
            content_hash="",
            normalized_text=None,
            normalized_html=None,
            http_status=None,
            renderer_metadata=None,
            error_category="not_implemented",
            provider_cost_estimate=0.05,
            provenance={'tier': 'monid', 'method': 'api'},
            tier_used="monid"
        )
    
    def _tier_4_apify_fetch(self, job: AcquisitionJob) -> AcquisitionResult:
        """
        Tier 4: Apify integration (placeholder)
        Would integrate with Apify actors for scale-out
        """
        # Placeholder for Apify integration
        # This would call Apify actors through the adapter pattern
        
        logger.warning(f"Apify integration not yet implemented for job {job.job_id}")
        
        return AcquisitionResult(
            job_id=job.job_id,
            status=AcquisitionStatus.PROVIDER_ERROR,
            final_url=job.url,
            retrieval_timestamp=datetime.utcnow(),
            content_type=None,
            content_length=0,
            content_hash="",
            normalized_text=None,
            normalized_html=None,
            http_status=None,
            renderer_metadata=None,
            error_category="not_implemented",
            provider_cost_estimate=0.10,
            provenance={'tier': 'apify', 'method': 'actor'},
            tier_used="apify"
        )
    
    def acquire(self, job: AcquisitionJob) -> AcquisitionResult:
        """
        Main acquisition method with tiered escalation
        
        Args:
            job: Acquisition job definition
            
        Returns:
            Acquisition result
        """
        canonical_url = self._canonicalize_url(job.url)
        
        # Check cache first
        cached = self._check_cache(canonical_url)
        if cached:
            return cached
        
        # Start with Tier 1 (HTTP)
        logger.info(f"Starting acquisition for {job.job_id} at Tier 1 (HTTP)")
        result = self._tier_1_http_fetch(job)
        
        # Escalate to Tier 2 if needed
        if result.status == AcquisitionStatus.FALLBACK_REQUIRED and job.budget_class in [BudgetClass.CHEAP_BROWSER, BudgetClass.MANAGED_STANDARD, BudgetClass.MANAGED_SCALE]:
            logger.info(f"Escalating {job.job_id} to Tier 2 (Playwright)")
            result = self._tier_2_playwright_fetch(job)
        
        # Escalate to Tier 3 if needed
        if result.status == AcquisitionStatus.FALLBACK_REQUIRED and job.budget_class in [BudgetClass.MANAGED_STANDARD, BudgetClass.MANAGED_SCALE]:
            logger.info(f"Escalating {job.job_id} to Tier 3 (Monid)")
            result = self._tier_3_monid_fetch(job)
        
        # Escalate to Tier 4 if needed
        if result.status == AcquisitionStatus.FALLBACK_REQUIRED and job.budget_class == BudgetClass.MANAGED_SCALE:
            logger.info(f"Escalating {job.job_id} to Tier 4 (Apify)")
            result = self._tier_4_apify_fetch(job)
        
        # Cache successful results
        if result.status == AcquisitionStatus.SUCCESS:
            self._update_cache(result)
        
        return result
    
    def get_cost_metrics(self) -> Dict[str, Any]:
        """Get cost tracking metrics"""
        return {
            'http_calls': self._cost_tracker['http_calls'],
            'browser_calls': self._cost_tracker['browser_calls'],
            'monid_calls': self._cost_tracker['monid_calls'],
            'apify_calls': self._cost_tracker['apify_calls'],
            'total_cost': self._cost_tracker['total_cost'],
            'cache_size': len(self._cache)
        }
    
    def clear_cache(self):
        """Clear the cache"""
        self._cache.clear()
        logger.info("Cache cleared")
    
    def close(self):
        """Cleanup resources"""
        if self._http_client:
            self._http_client.close()
            self._http_client = None
        
        if self._playwright_browser:
            self._playwright_browser.close()
            self._playwright_browser = None
        
        logger.info("Tiered scraper closed")
    
    def __enter__(self):
        """Context manager entry"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.close()
