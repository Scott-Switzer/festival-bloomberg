"""
C3 Festival Portfolio Registry
Implements C3 Presents festival portfolio with format-specific parsers per Festival Bloomberg spec
"""
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class FestivalFormat(Enum):
    """Festival lineup format profiles"""
    POSTER_GRID = "poster_grid"  # OCR from poster images (Lollapalooza, ACL)
    DAY_STAGE_SCHEDULE = "day_stage_schedule"  # Structured schedule JSON
    MULTI_WEEKEND = "multi_weekend"  # Multiple weekend editions
    GENRE_CURATED_GRID = "genre_curated_grid"  # Genre-based grid layout
    SIMPLE_LIST = "simple_list"  # Simple artist list
    UNKNOWN = "unknown"


class ProductionRole(Enum):
    """Production roles for festivals"""
    PRODUCER = "producer"
    CO_PRODUCER = "co_producer"
    PRESENTER = "presenter"
    PROMOTER = "promoter"
    OWNER = "owner"
    LOCAL_PARTNER = "local_partner"
    HISTORICAL_ASSOCIATION = "historical_association"


class Currency(Enum):
    """Currency codes"""
    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"
    CAD = "CAD"
    AUD = "AUD"
    BRL = "BRL"
    MXN = "MXN"
    ARS = "ARS"
    CLP = "CLP"
    COP = "COP"


@dataclass
class C3Festival:
    """C3 festival registration"""
    festival_id: str
    name: str
    property_family: str  # Lollapalooza, ACL, etc.
    format_profile: FestivalFormat
    default_country_code: str
    default_currency: Currency
    official_domain: str
    city: str
    region: str
    typical_month: int  # 1-12
    capacity: int
    primary_genres: List[str]
    active: bool = True
    international_editions: List[str] = field(default_factory=list)
    production_roles: Dict[str, ProductionRole] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


class C3PortfolioRegistry:
    """
    C3 Presents festival portfolio registry
    Implements Festival Bloomberg portfolio requirements
    """
    
    def __init__(self):
        self._festivals: Dict[str, C3Festival] = {}
        self._initialize_portfolio()
        
        logger.info("C3 portfolio registry initialized")
    
    def _initialize_portfolio(self):
        """Initialize C3 festival portfolio"""
        
        # Lollapalooza Chicago (Flagship)
        self._festivals["lolla_chicago"] = C3Festival(
            festival_id="lolla_chicago",
            name="Lollapalooza Chicago",
            property_family="Lollapalooza",
            format_profile=FestivalFormat.POSTER_GRID,
            default_country_code="US",
            default_currency=Currency.USD,
            official_domain="lollapalooza.com",
            city="Chicago",
            region="Illinois",
            typical_month=8,
            capacity=400_000,
            primary_genres=["rock", "electronic", "hip-hop", "alternative"],
            international_editions=["lolla_berlin", "lolla_buenos_aires", "lolla_sao_paulo", "lolla_india", "lolla_paris"],
            production_roles={
                "C3 Presents": ProductionRole.PRODUCER,
                "Perry Farrell": ProductionRole.OWNER
            },
            metadata={
                "venue": "Grant Park",
                "duration_days": 4,
                "weekend_count": 4,
                "founded_year": 1991
            }
        )
        
        # Austin City Limits Music Festival
        self._festivals["acl"] = C3Festival(
            festival_id="acl",
            name="Austin City Limits Music Festival",
            property_family="ACL",
            format_profile=FestivalFormat.POSTER_GRID,
            default_country_code="US",
            default_currency=Currency.USD,
            official_domain="aclfestival.com",
            city="Austin",
            region="Texas",
            typical_month=10,
            capacity=450_000,
            primary_genres=["rock", "indie", "folk", "electronic", "hip-hop"],
            international_editions=[],
            production_roles={
                "C3 Presents": ProductionRole.PRODUCER,
                "Austin City Limits": ProductionRole.PRESENTER
            },
            metadata={
                "venue": "Zilker Park",
                "duration_days": 6,
                "weekend_count": 2,
                "founded_year": 2002
            }
        )
        
        # Lollapalooza Berlin
        self._festivals["lolla_berlin"] = C3Festival(
            festival_id="lolla_berlin",
            name="Lollapalooza Berlin",
            property_family="Lollapalooza",
            format_profile=FestivalFormat.DAY_STAGE_SCHEDULE,
            default_country_code="DE",
            default_currency=Currency.EUR,
            official_domain="lollapalooza.com",
            city="Berlin",
            region="Berlin",
            typical_month=9,
            capacity=85_000,
            primary_genres=["rock", "electronic", "alternative", "hip-hop"],
            international_editions=[],
            production_roles={
                "C3 Presents": ProductionRole.PRODUCER,
                "Live Nation Germany": ProductionRole.LOCAL_PARTNER
            },
            metadata={
                "venue": "Olympiastadion & Olympiapark",
                "duration_days": 3,
                "weekend_count": 1,
                "founded_year": 2015
            }
        )
        
        # Lollapalooza Buenos Aires
        self._festivals["lolla_buenos_aires"] = C3Festival(
            festival_id="lolla_buenos_aires",
            name="Lollapalooza Buenos Aires",
            property_family="Lollapalooza",
            format_profile=FestivalFormat.DAY_STAGE_SCHEDULE,
            default_country_code="AR",
            default_currency=Currency.ARS,
            official_domain="lollapalooza.com",
            city="Buenos Aires",
            region="Buenos Aires",
            typical_month=3,
            capacity=80_000,
            primary_genres=["rock", "electronic", "alternative", "latin"],
            international_editions=[],
            production_roles={
                "C3 Presents": ProductionRole.PRODUCER,
                "DF Entertainment": ProductionRole.LOCAL_PARTNER
            },
            metadata={
                "venue": "Hipódromo de San Isidro",
                "duration_days": 3,
                "weekend_count": 1,
                "founded_year": 2011
            }
        )
        
        # Lollapalooza São Paulo
        self._festivals["lolla_sao_paulo"] = C3Festival(
            festival_id="lolla_sao_paulo",
            name="Lollapalooza São Paulo",
            property_family="Lollapalooza",
            format_profile=FestivalFormat.DAY_STAGE_SCHEDULE,
            default_country_code="BR",
            default_currency=Currency.BRL,
            official_domain="lollapalooza.com",
            city="São Paulo",
            region="São Paulo",
            typical_month=3,
            capacity=100_000,
            primary_genres=["rock", "electronic", "alternative", "brazilian"],
            international_editions=[],
            production_roles={
                "C3 Presents": ProductionRole.PRODUCER,
                "Time 4 Fun": ProductionRole.LOCAL_PARTNER
            },
            metadata={
                "venue": "Autódromo de Interlagos",
                "duration_days": 3,
                "weekend_count": 1,
                "founded_year": 2012
            }
        )
        
        # Lollapalooza India
        self._festivals["lolla_india"] = C3Festival(
            festival_id="lolla_india",
            name="Lollapalooza India",
            property_family="Lollapalooza",
            format_profile=FestivalFormat.DAY_STAGE_SCHEDULE,
            default_country_code="IN",
            default_currency=Currency.USD,  # International pricing
            official_domain="lollapalooza.com",
            city="Mumbai",
            region="Maharashtra",
            typical_month=1,
            capacity=60_000,
            primary_genres=["rock", "electronic", "indian", "hip-hop"],
            international_editions=[],
            production_roles={
                "C3 Presents": ProductionRole.PRODUCER,
                "BookMyShow": ProductionRole.LOCAL_PARTNER
            },
            metadata={
                "venue": "Mahalaxmi Racecourse",
                "duration_days": 3,
                "weekend_count": 1,
                "founded_year": 2023
            }
        )
        
        # Lollapalooza Paris
        self._festivals["lolla_paris"] = C3Festival(
            festival_id="lolla_paris",
            name="Lollapalooza Paris",
            property_family="Lollapalooza",
            format_profile=FestivalFormat.DAY_STAGE_SCHEDULE,
            default_country_code="FR",
            default_currency=Currency.EUR,
            official_domain="lollapalooza.com",
            city="Paris",
            region="Île-de-France",
            typical_month=7,
            capacity=75_000,
            primary_genres=["rock", "electronic", "alternative", "french"],
            international_editions=[],
            production_roles={
                "C3 Presents": ProductionRole.PRODUCER,
                "Live Nation France": ProductionRole.LOCAL_PARTNER
            },
            metadata={
                "venue": "Hippodrome de Longchamp",
                "duration_days": 3,
                "weekend_count": 1,
                "founded_year": 2022
            }
        )
        
        # Additional C3 festivals to be added as needed
        # This is a representative sample of the full portfolio
        
        logger.info(f"Initialized {len(self._festivals)} C3 festivals")
    
    def get_festival(self, festival_id: str) -> Optional[C3Festival]:
        """Get festival by ID"""
        return self._festivals.get(festival_id)
    
    def list_festivals(self, 
                      property_family: Optional[str] = None,
                      country_code: Optional[str] = None,
                      active_only: bool = True) -> List[C3Festival]:
        """
        List festivals with optional filters
        
        Args:
            property_family: Filter by property family
            country_code: Filter by country code
            active_only: Only return active festivals
            
        Returns:
            List of festivals
        """
        festivals = list(self._festivals.values())
        
        if property_family:
            festivals = [f for f in festivals if f.property_family == property_family]
        
        if country_code:
            festivals = [f for f in festivals if f.default_country_code == country_code]
        
        if active_only:
            festivals = [f for f in festivals if f.active]
        
        return festivals
    
    def get_property_families(self) -> List[str]:
        """Get all property families"""
        return list(set(f.property_family for f in self._festivals.values()))
    
    def get_international_editions(self, festival_id: str) -> List[C3Festival]:
        """Get international editions for a festival"""
        festival = self.get_festival(festival_id)
        if not festival:
            return []
        
        return [self.get_festival(edition_id) for edition_id in festival.international_editions]
    
    def get_format_parser(self, format_profile: FestivalFormat):
        """
        Get the appropriate parser for a format profile
        
        Args:
            format_profile: Festival format profile
            
        Returns:
            Parser class
        """
        from .format_parsers import (
            PosterGridParser,
            DayStageScheduleParser,
            MultiWeekendParser,
            GenreCuratedGridParser,
            SimpleListParser
        )
        
        parsers = {
            FestivalFormat.POSTER_GRID: PosterGridParser,
            FestivalFormat.DAY_STAGE_SCHEDULE: DayStageScheduleParser,
            FestivalFormat.MULTI_WEEKEND: MultiWeekendParser,
            FestivalFormat.GENRE_CURATED_GRID: GenreCuratedGridParser,
            FestivalFormat.SIMPLE_LIST: SimpleListParser
        }
        
        return parsers.get(format_profile, SimpleListParser)
    
    def register_festival(self, festival: C3Festival):
        """Register a new festival"""
        self._festivals[festival.festival_id] = festival
        logger.info(f"Registered festival: {festival.festival_id}")
    
    def update_festival_status(self, festival_id: str, active: bool):
        """Update festival active status"""
        festival = self.get_festival(festival_id)
        if festival:
            festival.active = active
            logger.info(f"Updated festival status: {festival_id} -> {active}")
    
    def get_portfolio_stats(self) -> Dict[str, Any]:
        """Get portfolio statistics"""
        festivals = list(self._festivals.values())
        
        return {
            "total_festivals": len(festivals),
            "active_festivals": sum(1 for f in festivals if f.active),
            "property_families": self.get_property_families(),
            "countries": list(set(f.default_country_code for f in festivals)),
            "total_capacity": sum(f.capacity for f in festivals),
            "by_format": {
                format.value: sum(1 for f in festivals if f.format_profile == format)
                for format in FestivalFormat
            }
        }


def create_c3_portfolio() -> C3PortfolioRegistry:
    """Factory function to create C3 portfolio registry"""
    return C3PortfolioRegistry()
