"""
Source Eligibility Registry for Festival Bloomberg

Machine-readable metadata for all data sources to ensure legal and commercial compliance.
Every source must have documented eligibility before production use.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Any
from datetime import datetime


class CommercialUseStatus(Enum):
    """Commercial use status classifications"""
    OPEN_COMMERCIAL_OK = "OPEN_COMMERCIAL_OK"
    OPEN_WITH_ATTRIBUTION = "OPEN_WITH_ATTRIBUTION"
    FREE_RESEARCH_ONLY = "FREE_RESEARCH_ONLY"
    NONCOMMERCIAL_ONLY = "NONCOMMERCIAL_ONLY"
    COMMERCIAL_AGREEMENT_REQUIRED = "COMMERCIAL_AGREEMENT_REQUIRED"
    PARTNER_ACCESS_REQUIRED = "PARTNER_ACCESS_REQUIRED"
    TERMS_REVIEW_REQUIRED = "TERMS_REVIEW_REQUIRED"
    PROHIBITED = "PROHIBITED"
    UNKNOWN = "UNKNOWN"


class CostClass(Enum):
    """Cost classification"""
    FREE = "FREE"
    FREE_WITH_QUOTA = "FREE_WITH_QUOTA"
    FREE_TIER_AVAILABLE = "FREE_TIER_AVAILABLE"
    PAID_SUBSCRIPTION = "PAID_SUBSCRIPTION"
    USAGE_BASED = "USAGE_BASED"
    ENTERPRISE_LICENSING = "ENTERPRISE_LICENSING"
    CUSTOM_PRICING = "CUSTOM_PRICING"
    UNKNOWN = "UNKNOWN"


class AccessType(Enum):
    """Access type classification"""
    PUBLIC_API = "PUBLIC_API"
    AUTHENTICATED_API = "AUTHENTICATED_API"
    OAUTH_REQUIRED = "OAUTH_REQUIRED"
    API_KEY_REQUIRED = "API_KEY_REQUIRED"
    DATA_DOWNLOAD = "DATA_DOWNLOAD"
    WEB_SCRAPING = "WEB_SCRAPING"
    PARTNER_PORTAL = "PARTNER_PORTAL"
    MANUAL = "MANUAL"
    UNKNOWN = "UNKNOWN"


class SourceCategory(Enum):
    """Source category classification"""
    ARTIST_ENTITY = "ARTIST_ENTITY"
    ATTENTION = "ATTENTION"
    NEWS_SENTIMENT = "NEWS_SENTIMENT"
    TICKET_EVENT = "TICKET_EVENT"
    VIDEO = "VIDEO"
    CONCERT_HISTORY = "CONCERT_HISTORY"
    TOURING_DISCOVERY = "TOURING_DISCOVERY"
    WEATHER = "WEATHER"
    AIR_TRAVEL = "AIR_TRAVEL"
    DEMOGRAPHICS = "DEMOGRAPHICS"
    REGIONAL_ECONOMICS = "REGIONAL_ECONOMICS"
    LABOR_ECONOMICS = "LABOR_ECONOMICS"
    FX = "FX"
    GEOGRAPHY = "GEOGRAPHY"
    SOCIAL_MEDIA = "SOCIAL_MEDIA"
    STREAMING = "STREAMING"
    RADIO = "RADIO"
    MARKET_DATA = "MARKET_DATA"
    UNKNOWN = "UNKNOWN"


@dataclass
class SourceMetadata:
    """Machine-readable metadata for data sources"""
    source_id: str
    name: str
    base_url: str
    category: SourceCategory
    access_type: AccessType
    auth_required: bool
    rate_limit: Optional[str] = None
    cost_class: CostClass = CostClass.UNKNOWN
    portfolio_research_allowed: bool = False
    academic_allowed: bool = False
    commercial_use_status: CommercialUseStatus = CommercialUseStatus.UNKNOWN
    redistribution_status: str = "UNKNOWN"
    raw_storage_status: str = "UNKNOWN"
    derived_data_status: str = "UNKNOWN"
    attribution_required: bool = False
    terms_url: Optional[str] = None
    license_url: Optional[str] = None
    terms_checked_at: Optional[datetime] = None
    legal_review_status: str = "NOT_REVIEWED"
    notes: str = ""
    historical_depth: Optional[str] = None
    geographic_coverage: Optional[str] = None
    update_frequency: Optional[str] = None
    confidence: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def is_production_eligible(self) -> bool:
        """Check if source is eligible for production use"""
        if self.commercial_use_status in [
            CommercialUseStatus.PROHIBITED,
            CommercialUseStatus.UNKNOWN
        ]:
            return False
        
        if self.legal_review_status != "APPROVED":
            return False
        
        if not self.portfolio_research_allowed:
            return False
        
        return True
    
    def is_research_only(self) -> bool:
        """Check if source is research-only"""
        return self.commercial_use_status in [
            CommercialUseStatus.FREE_RESEARCH_ONLY,
            CommercialUseStatus.NONCOMMERCIAL_ONLY
        ]
    
    def requires_agreement(self) -> bool:
        """Check if source requires commercial agreement"""
        return self.commercial_use_status in [
            CommercialUseStatus.COMMERCIAL_AGREEMENT_REQUIRED,
            CommercialUseStatus.PARTNER_ACCESS_REQUIRED
        ]


class SourceRegistry:
    """Registry for managing source eligibility metadata"""
    
    def __init__(self):
        self.sources: Dict[str, SourceMetadata] = {}
        self._initialize_default_sources()
    
    def _initialize_default_sources(self):
        """Initialize with known sources from research"""
        
        # High-confidence public sources
        self.register(SourceMetadata(
            source_id="wikidata",
            name="Wikidata",
            base_url="https://www.wikidata.org",
            category=SourceCategory.ARTIST_ENTITY,
            access_type=AccessType.PUBLIC_API,
            auth_required=False,
            rate_limit=None,
            cost_class=CostClass.FREE,
            portfolio_research_allowed=True,
            academic_allowed=True,
            commercial_use_status=CommercialUseStatus.OPEN_COMMERCIAL_OK,
            redistribution_status="CC0_1.0",
            raw_storage_status="CC0_1.0",
            derived_data_status="CC0_1.0",
            attribution_required=False,
            terms_url="https://www.wikidata.org/wiki/Wikidata:Licensing",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
            terms_checked_at=datetime(2026, 8, 12),
            legal_review_status="APPROVED",
            historical_depth="Extensive",
            geographic_coverage="Global",
            update_frequency="Continuous",
            confidence=0.95,
            notes="CC0 public domain license, no attribution required. Excellent for canonical entity resolution."
        ))
        
        self.register(SourceMetadata(
            source_id="wikimedia_analytics",
            name="Wikimedia Analytics API",
            base_url="https://doc.wikimedia.org/generated-data-platform/aqs/analytics-api/",
            category=SourceCategory.ATTENTION,
            access_type=AccessType.PUBLIC_API,
            auth_required=False,
            rate_limit=None,
            cost_class=CostClass.FREE,
            portfolio_research_allowed=True,
            academic_allowed=True,
            commercial_use_status=CommercialUseStatus.OPEN_WITH_ATTRIBUTION,
            redistribution_status="VARIES_BY_PROJECT",
            raw_storage_status="VARIES_BY_PROJECT",
            derived_data_status="VARIES_BY_PROJECT",
            attribution_required=True,
            terms_url="https://doc.wikimedia.org/generated-data-platform/aqs/analytics-api/",
            license_url="https://creativecommons.org/licenses/by-sa/3.0/",
            terms_checked_at=datetime(2026, 8, 12),
            legal_review_status="APPROVED",
            historical_depth="Several years",
            geographic_coverage="Global (by project)",
            update_frequency="Daily",
            confidence=0.90,
            notes="Pageview data for Wikipedia articles. License varies by project, attribution required."
        ))
        
        self.register(SourceMetadata(
            source_id="gdelt",
            name="GDELT Project",
            base_url="https://www.gdeltproject.org",
            category=SourceCategory.NEWS_SENTIMENT,
            access_type=AccessType.PUBLIC_API,
            auth_required=False,
            rate_limit=None,
            cost_class=CostClass.FREE,
            portfolio_research_allowed=True,
            academic_allowed=True,
            commercial_use_status=CommercialUseStatus.OPEN_WITH_ATTRIBUTION,
            redistribution_status="CC_BY_4.0",
            raw_storage_status="CC_BY_4.0",
            derived_data_status="CC_BY_4.0",
            attribution_required=True,
            terms_url="https://www.gdeltproject.org/about.html",
            license_url="https://creativecommons.org/licenses/by/4.0/",
            terms_checked_at=datetime(2026, 8, 12),
            legal_review_status="APPROVED",
            historical_depth="1979-present",
            geographic_coverage="Global",
            update_frequency="Every 15 minutes",
            confidence=0.90,
            notes="Massive news event database, excellent for sentiment analysis. CC BY 4.0 with attribution."
        ))
        
        self.register(SourceMetadata(
            source_id="noaa_nws",
            name="NOAA National Weather Service",
            base_url="https://www.weather.gov/documentation/services-web-alerts",
            category=SourceCategory.WEATHER,
            access_type=AccessType.PUBLIC_API,
            auth_required=False,
            rate_limit=None,
            cost_class=CostClass.FREE,
            portfolio_research_allowed=True,
            academic_allowed=True,
            commercial_use_status=CommercialUseStatus.OPEN_COMMERCIAL_OK,
            redistribution_status="PUBLIC_DOMAIN",
            raw_storage_status="PUBLIC_DOMAIN",
            derived_data_status="PUBLIC_DOMAIN",
            attribution_required=False,
            terms_url="https://www.weather.gov/documentation/services-web-alerts",
            license_url="https://www.noaa.gov/weather-atmosphere-data",
            terms_checked_at=datetime(2026, 8, 12),
            legal_review_status="APPROVED",
            historical_depth="Extensive",
            geographic_coverage="United States",
            update_frequency="Real-time",
            confidence=0.95,
            notes="Public domain weather data. Excellent for US weather risk analysis."
        ))
        
        self.register(SourceMetadata(
            source_id="noaa_ncei",
            name="NOAA NCEI Climate Data",
            base_url="https://www.ncei.noaa.gov/support/access-data-service-api-user-documentation",
            category=SourceCategory.WEATHER,
            access_type=AccessType.AUTHENTICATED_API,
            auth_required=False,
            rate_limit=None,
            cost_class=CostClass.FREE,
            portfolio_research_allowed=True,
            academic_allowed=True,
            commercial_use_status=CommercialUseStatus.OPEN_COMMERCIAL_OK,
            redistribution_status="PUBLIC_DOMAIN",
            raw_storage_status="PUBLIC_DOMAIN",
            derived_data_status="PUBLIC_DOMAIN",
            attribution_required=False,
            terms_url="https://www.ncei.noaa.gov/support/access-data-service-api-user-documentation",
            license_url="https://www.noaa.gov/weather-atmosphere-data",
            terms_checked_at=datetime(2026, 8, 12),
            legal_review_status="APPROVED",
            historical_depth="Extensive",
            geographic_coverage="Global",
            update_frequency="Monthly",
            confidence=0.95,
            notes="Historical weather data for backtesting. Public domain."
        ))
        
        self.register(SourceMetadata(
            source_id="bts_transtats",
            name="BTS TranStats",
            base_url="https://www.transtats.bts.gov/",
            category=SourceCategory.AIR_TRAVEL,
            access_type=AccessType.DATA_DOWNLOAD,
            auth_required=False,
            rate_limit=None,
            cost_class=CostClass.FREE,
            portfolio_research_allowed=True,
            academic_allowed=True,
            commercial_use_status=CommercialUseStatus.OPEN_COMMERCIAL_OK,
            redistribution_status="PUBLIC_DOMAIN",
            raw_storage_status="PUBLIC_DOMAIN",
            derived_data_status="PUBLIC_DOMAIN",
            attribution_required=False,
            terms_url="https://www.transtats.bts.gov/",
            license_url="https://www.transportation.gov/policy/accessibility",
            terms_checked_at=datetime(2026, 8, 12),
            legal_review_status="APPROVED",
            historical_depth="Extensive",
            geographic_coverage="United States",
            update_frequency="Monthly",
            confidence=0.90,
            notes="Air travel statistics for routing analysis. Public domain."
        ))
        
        self.register(SourceMetadata(
            source_id="census_acs",
            name="US Census ACS",
            base_url="https://www.census.gov/data/developers/data-sets/acs-5year.html",
            category=SourceCategory.DEMOGRAPHICS,
            access_type=AccessType.AUTHENTICATED_API,
            auth_required=True,
            rate_limit=None,
            cost_class=CostClass.FREE,
            portfolio_research_allowed=True,
            academic_allowed=True,
            commercial_use_status=CommercialUseStatus.OPEN_WITH_ATTRIBUTION,
            redistribution_status="PUBLIC_DOMAIN",
            raw_storage_status="PUBLIC_DOMAIN",
            derived_data_status="PUBLIC_DOMAIN",
            attribution_required=True,
            terms_url="https://www.census.gov/data/developers/data-sets/acs-5year.html",
            license_url="https://www.census.gov/about/policies/privacy/data-stewardship",
            terms_checked_at=datetime(2026, 8, 12),
            legal_review_status="APPROVED",
            historical_depth="5-year estimates",
            geographic_coverage="United States",
            update_frequency="Annual",
            confidence=0.90,
            notes="Detailed demographic data for market analysis. Public domain with attribution."
        ))
        
        self.register(SourceMetadata(
            source_id="bea_api",
            name="BEA API",
            base_url="https://apps.bea.gov/api/signup/",
            category=SourceCategory.REGIONAL_ECONOMICS,
            access_type=AccessType.AUTHENTICATED_API,
            auth_required=True,
            rate_limit="120 requests/minute",
            cost_class=CostClass.FREE,
            portfolio_research_allowed=True,
            academic_allowed=True,
            commercial_use_status=CommercialUseStatus.OPEN_WITH_ATTRIBUTION,
            redistribution_status="PUBLIC_DOMAIN",
            raw_storage_status="PUBLIC_DOMAIN",
            derived_data_status="PUBLIC_DOMAIN",
            attribution_required=True,
            terms_url="https://apps.bea.gov/api/signup/",
            license_url="https://www.bea.gov/about/policies",
            terms_checked_at=datetime(2026, 8, 12),
            legal_review_status="APPROVED",
            historical_depth="Extensive",
            geographic_coverage="United States",
            update_frequency="Quarterly/Annual",
            confidence=0.90,
            notes="Regional economic indicators. Public domain with attribution."
        ))
        
        self.register(SourceMetadata(
            source_id="bls_api",
            name="BLS API",
            base_url="https://www.bls.gov/developers/home.htm",
            category=SourceCategory.LABOR_ECONOMICS,
            access_type=AccessType.AUTHENTICATED_API,
            auth_required=True,
            rate_limit="Unknown (reasonable use policy)",
            cost_class=CostClass.FREE,
            portfolio_research_allowed=True,
            academic_allowed=True,
            commercial_use_status=CommercialUseStatus.OPEN_WITH_ATTRIBUTION,
            redistribution_status="PUBLIC_DOMAIN",
            raw_storage_status="PUBLIC_DOMAIN",
            derived_data_status="PUBLIC_DOMAIN",
            attribution_required=True,
            terms_url="https://www.bls.gov/developers/home.htm",
            license_url="https://www.bls.gov/about/policies",
            terms_checked_at=datetime(2026, 8, 12),
            legal_review_status="APPROVED",
            historical_depth="Extensive",
            geographic_coverage="United States",
            update_frequency="Monthly/Annual",
            confidence=0.90,
            notes="Labor market and economic indicators. Public domain with attribution."
        ))
        
        self.register(SourceMetadata(
            source_id="ecb_api",
            name="ECB API",
            base_url="https://data.ecb.europa.eu/help/api/overview",
            category=SourceCategory.FX,
            access_type=AccessType.PUBLIC_API,
            auth_required=False,
            rate_limit=None,
            cost_class=CostClass.FREE,
            portfolio_research_allowed=True,
            academic_allowed=True,
            commercial_use_status=CommercialUseStatus.OPEN_COMMERCIAL_OK,
            redistribution_status="PUBLIC_DOMAIN",
            raw_storage_status="PUBLIC_DOMAIN",
            derived_data_status="PUBLIC_DOMAIN",
            attribution_required=False,
            terms_url="https://data.ecb.europa.eu/help/api/overview",
            license_url="https://www.ecb.europa.eu/home/policy/legal",
            terms_checked_at=datetime(2026, 8, 12),
            legal_review_status="APPROVED",
            historical_depth="Extensive",
            geographic_coverage="Global (major currencies)",
            update_frequency="Daily",
            confidence=0.95,
            notes="Exchange rate data for international festival economics. Public domain."
        ))
        
        self.register(SourceMetadata(
            source_id="openstreetmap",
            name="OpenStreetMap",
            base_url="https://www.openstreetmap.org/copyright",
            category=SourceCategory.GEOGRAPHY,
            access_type=AccessType.PUBLIC_API,
            auth_required=False,
            rate_limit=None,
            cost_class=CostClass.FREE,
            portfolio_research_allowed=True,
            academic_allowed=True,
            commercial_use_status=CommercialUseStatus.OPEN_WITH_ATTRIBUTION,
            redistribution_status="ODBL",
            raw_storage_status="ODBL",
            derived_data_status="ODBL",
            attribution_required=True,
            terms_url="https://www.openstreetmap.org/copyright",
            license_url="https://opendatacommons.org/licenses/odbl/1-0/",
            terms_checked_at=datetime(2026, 8, 12),
            legal_review_status="APPROVED",
            historical_depth="Extensive",
            geographic_coverage="Global",
            update_frequency="Continuous",
            confidence=0.90,
            notes="Geographic data for venue and routing analysis. ODbL with attribution."
        ))
        
        # Medium-confidence sources (attribution/share-alike requirements)
        self.register(SourceMetadata(
            source_id="musicbrainz",
            name="MusicBrainz",
            base_url="https://musicbrainz.org/doc/MusicBrainz_API",
            category=SourceCategory.ARTIST_ENTITY,
            access_type=AccessType.PUBLIC_API,
            auth_required=False,
            rate_limit="1 req/sec",
            cost_class=CostClass.FREE,
            portfolio_research_allowed=True,
            academic_allowed=True,
            commercial_use_status=CommercialUseStatus.TERMS_REVIEW_REQUIRED,
            redistribution_status="CC_BY_SA_4.0",
            raw_storage_status="CC_BY_SA_4.0",
            derived_data_status="CC_BY_SA_4.0",
            attribution_required=True,
            terms_url="https://musicbrainz.org/doc/MusicBrainz_API",
            license_url="https://creativecommons.org/licenses/by-sa/4.0/",
            terms_checked_at=datetime(2026, 8, 12),
            legal_review_status="PENDING_REVIEW",
            historical_depth="Extensive (founded 2000)",
            geographic_coverage="Global",
            update_frequency="Continuous",
            confidence=0.70,
            notes="CC BY-SA 4.0 share-alike license may restrict commercial SaaS use. Legal review required."
        ))
        
        self.register(SourceMetadata(
            source_id="listenbrainz",
            name="ListenBrainz",
            base_url="https://listenbrainz.org",
            category=SourceCategory.ATTENTION,
            access_type=AccessType.DATA_DOWNLOAD,
            auth_required=False,
            rate_limit=None,
            cost_class=CostClass.FREE,
            portfolio_research_allowed=True,
            academic_allowed=True,
            commercial_use_status=CommercialUseStatus.OPEN_WITH_ATTRIBUTION,
            redistribution_status="OPEN_DATA",
            raw_storage_status="OPEN_DATA",
            derived_data_status="OPEN_DATA",
            attribution_required=True,
            terms_url="https://listenbrainz.readthedocs.io/",
            license_url="https://metabrainz.org/datasets",
            terms_checked_at=datetime(2026, 8, 19),
            legal_review_status="APPROVED",
            historical_depth="2015-present",
            geographic_coverage="Global",
            update_frequency="Continuous",
            confidence=0.85,
            notes="Listen data is open data and available for commercial use (official server README: 'All of our data is available for commercial use'); the GPL license covers the server CODE, not the data. Use AGGREGATED artist-level attention only — never build user-level profiles. Attribution/support requested."
        ))
        
        # Low-confidence sources (commercial restrictions)
        self.register(SourceMetadata(
            source_id="ticketmaster_api",
            name="Ticketmaster Discovery API",
            base_url="https://developer.ticketmaster.com/products-and-docs/apis/discovery-manual/v2/",
            category=SourceCategory.TICKET_EVENT,
            access_type=AccessType.AUTHENTICATED_API,
            auth_required=True,
            rate_limit="Documented (varies by plan)",
            cost_class=CostClass.ENTERPRISE_LICENSING,
            portfolio_research_allowed=False,
            academic_allowed=False,
            commercial_use_status=CommercialUseStatus.COMMERCIAL_AGREEMENT_REQUIRED,
            redistribution_status="PROPRIETARY",
            raw_storage_status="PROPRIETARY",
            derived_data_status="PROPRIETARY",
            attribution_required=True,
            terms_url="https://developer.ticketmaster.com/support/terms-of-use",
            license_url=None,
            terms_checked_at=datetime(2026, 8, 12),
            legal_review_status="REQUIRES_AGREEMENT",
            historical_depth="Current events + some historical",
            geographic_coverage="Global (30+ countries)",
            update_frequency="Real-time",
            confidence=0.60,
            notes="Primary source but restricted access. Commercial agreement required."
        ))
        
        self.register(SourceMetadata(
            source_id="setlist_fm",
            name="Setlist.fm API",
            base_url="https://api.setlist.fm/docs/1.0/index.html",
            category=SourceCategory.CONCERT_HISTORY,
            access_type=AccessType.AUTHENTICATED_API,
            auth_required=True,
            rate_limit="Documented",
            cost_class=CostClass.CUSTOM_PRICING,
            portfolio_research_allowed=True,
            academic_allowed=True,
            commercial_use_status=CommercialUseStatus.COMMERCIAL_AGREEMENT_REQUIRED,
            redistribution_status="PROPRIETARY",
            raw_storage_status="PROPRIETARY",
            derived_data_status="PROPRIETARY",
            attribution_required=True,
            terms_url="https://api.setlist.fm/docs/1.0/index.html",
            license_url=None,
            terms_checked_at=datetime(2026, 8, 12),
            legal_review_status="REQUIRES_AGREEMENT",
            historical_depth="Extensive concert history",
            geographic_coverage="Global",
            update_frequency="Continuous",
            confidence=0.65,
            notes="Excellent for concert history but commercial restrictions. Free for development, paid for commercial."
        ))
        
        self.register(SourceMetadata(
            source_id="youtube_api",
            name="YouTube Data API",
            base_url="https://developers.google.com/youtube/v3/getting-started",
            category=SourceCategory.VIDEO,
            access_type=AccessType.OAUTH_REQUIRED,
            auth_required=True,
            rate_limit="10,000 units/day (free tier)",
            cost_class=CostClass.FREE_WITH_QUOTA,
            portfolio_research_allowed=True,
            academic_allowed=True,
            commercial_use_status=CommercialUseStatus.TERMS_REVIEW_REQUIRED,
            redistribution_status="YOUTUBE_TOS",
            raw_storage_status="YOUTUBE_TOS",
            derived_data_status="YOUTUBE_TOS",
            attribution_required=True,
            terms_url="https://developers.google.com/youtube/v3/getting-started",
            license_url="https://www.youtube.com/t/terms",
            terms_checked_at=datetime(2026, 8, 12),
            legal_review_status="PENDING_REVIEW",
            historical_depth="Extensive",
            geographic_coverage="Global",
            update_frequency="Real-time",
            confidence=0.75,
            notes="Excellent for video engagement metrics. Quota limits, terms of service restrictions. Legal review recommended."
        ))
        
        self.register(SourceMetadata(
            source_id="songkick",
            name="Songkick API",
            base_url="https://www.songkick.com/developer/",
            category=SourceCategory.TOURING_DISCOVERY,
            access_type=AccessType.AUTHENTICATED_API,
            auth_required=True,
            rate_limit="Documented",
            cost_class=CostClass.CUSTOM_PRICING,
            portfolio_research_allowed=True,
            academic_allowed=True,
            commercial_use_status=CommercialUseStatus.COMMERCIAL_AGREEMENT_REQUIRED,
            redistribution_status="PROPRIETARY",
            raw_storage_status="PROPRIETARY",
            derived_data_status="PROPRIETARY",
            attribution_required=True,
            terms_url="https://www.songkick.com/developer/",
            license_url=None,
            terms_checked_at=datetime(2026, 8, 12),
            legal_review_status="REQUIRES_AGREEMENT",
            historical_depth="Extensive",
            geographic_coverage="Global",
            update_frequency="Continuous",
            confidence=0.50,
            notes="Good tour discovery data but access unclear. Commercial agreement likely required."
        ))
    
    def register(self, source: SourceMetadata) -> None:
        """Register a source in the registry"""
        self.sources[source.source_id] = source
    
    def get(self, source_id: str) -> Optional[SourceMetadata]:
        """Get source metadata by ID"""
        return self.sources.get(source_id)
    
    def get_production_eligible(self) -> List[SourceMetadata]:
        """Get all sources eligible for production use"""
        return [s for s in self.sources.values() if s.is_production_eligible()]
    
    def get_research_only(self) -> List[SourceMetadata]:
        """Get all research-only sources"""
        return [s for s in self.sources.values() if s.is_research_only()]
    
    def get_requires_agreement(self) -> List[SourceMetadata]:
        """Get all sources requiring commercial agreement"""
        return [s for s in self.sources.values() if s.requires_agreement()]
    
    def get_by_category(self, category: SourceCategory) -> List[SourceMetadata]:
        """Get all sources by category"""
        return [s for s in self.sources.values() if s.category == category]
    
    def validate_source_usage(self, source_id: str, use_case: str = "production") -> bool:
        """
        Validate if a source can be used for a specific use case
        
        Args:
            source_id: Source identifier
            use_case: "production", "research", "portfolio", "academic"
            
        Returns:
            True if source can be used for the specified use case
        """
        source = self.get(source_id)
        if not source:
            return False
        
        if use_case == "production":
            return source.is_production_eligible()
        elif use_case == "research":
            return source.academic_allowed or source.portfolio_research_allowed
        elif use_case == "portfolio":
            return source.portfolio_research_allowed
        elif use_case == "academic":
            return source.academic_allowed
        else:
            return False
    
    def export_to_yaml(self, file_path: str) -> None:
        """Export registry to YAML file"""
        sources_dict = {}
        for source_id, source in self.sources.items():
            sources_dict[source_id] = {
                'source_id': source.source_id,
                'name': source.name,
                'base_url': source.base_url,
                'category': source.category.value,
                'access_type': source.access_type.value,
                'auth_required': source.auth_required,
                'rate_limit': source.rate_limit,
                'cost_class': source.cost_class.value,
                'portfolio_research_allowed': source.portfolio_research_allowed,
                'academic_allowed': source.academic_allowed,
                'commercial_use_status': source.commercial_use_status.value,
                'redistribution_status': source.redistribution_status,
                'raw_storage_status': source.raw_storage_status,
                'derived_data_status': source.derived_data_status,
                'attribution_required': source.attribution_required,
                'terms_url': source.terms_url,
                'license_url': source.license_url,
                'terms_checked_at': source.terms_checked_at.isoformat() if source.terms_checked_at else None,
                'legal_review_status': source.legal_review_status,
                'notes': source.notes,
                'historical_depth': source.historical_depth,
                'geographic_coverage': source.geographic_coverage,
                'update_frequency': source.update_frequency,
                'confidence': source.confidence,
                'metadata': source.metadata
            }
        
        with open(file_path, 'w') as f:
            yaml.dump(sources_dict, f, default_flow_style=False)
    
    def import_from_yaml(self, file_path: str) -> None:
        """Import registry from YAML file"""
        with open(file_path, 'r') as f:
            sources_dict = yaml.safe_load(f)
        
        for source_id, data in sources_dict.items():
            source = SourceMetadata(
                source_id=data['source_id'],
                name=data['name'],
                base_url=data['base_url'],
                category=SourceCategory(data['category']),
                access_type=AccessType(data['access_type']),
                auth_required=data['auth_required'],
                rate_limit=data.get('rate_limit'),
                cost_class=CostClass(data.get('cost_class', 'UNKNOWN')),
                portfolio_research_allowed=data['portfolio_research_allowed'],
                academic_allowed=data['academic_allowed'],
                commercial_use_status=CommercialUseStatus(data['commercial_use_status']),
                redistribution_status=data.get('redistribution_status', 'UNKNOWN'),
                raw_storage_status=data.get('raw_storage_status', 'UNKNOWN'),
                derived_data_status=data.get('derived_data_status', 'UNKNOWN'),
                attribution_required=data.get('attribution_required', False),
                terms_url=data.get('terms_url'),
                license_url=data.get('license_url'),
                terms_checked_at=datetime.fromisoformat(data['terms_checked_at']) if data.get('terms_checked_at') else None,
                legal_review_status=data.get('legal_review_status', 'NOT_REVIEWED'),
                notes=data.get('notes', ''),
                historical_depth=data.get('historical_depth'),
                geographic_coverage=data.get('geographic_coverage'),
                update_frequency=data.get('update_frequency'),
                confidence=data.get('confidence', 0.0),
                metadata=data.get('metadata', {})
            )
            self.register(source)


# Global registry instance
_registry: Optional[SourceRegistry] = None


def get_source_registry() -> SourceRegistry:
    """Get global source registry instance"""
    global _registry
    if _registry is None:
        _registry = SourceRegistry()
    return _registry


def reset_source_registry():
    """Reset global registry (useful for testing)"""
    global _registry
    _registry = None