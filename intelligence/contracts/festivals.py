"""
Festival-specific data contracts.
Includes initial festival selection and lineup data structures.
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from .entities import BillingTier, DataSource, MetricType


class FestivalLineup(BaseModel):
    """Festival lineup for a specific year"""
    festival_id: str = Field(..., description="Festival ID")
    year: int = Field(..., ge=2000, description="Festival year")
    
    # Lineup entries
    lineup_entries: List['LineupEntry'] = Field(default_factory=list, description="Artist lineup entries")
    
    # Lineup metadata
    total_artists: int = Field(default=0, ge=0, description="Total number of artists")
    headliners: List[str] = Field(default_factory=list, description="Headliner artist IDs")
    
    # Data provenance
    source: DataSource = Field(..., description="Data source")
    retrieved_at: datetime = Field(default_factory=datetime.utcnow)
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class LineupEntry(BaseModel):
    """Single artist entry in a festival lineup"""
    artist_id: str = Field(..., description="Artist ID")
    festival_id: str = Field(..., description="Festival ID")
    year: int = Field(..., ge=2000, description="Festival year")
    
    # Billing information
    billing_tier: BillingTier = Field(..., description="Billing tier")
    day_of_festival: Optional[int] = Field(None, ge=1, description="Day of multi-day festival")
    stage: Optional[str] = Field(None, description="Stage name")
    
    # Data provenance
    source: DataSource = Field(..., description="Data source")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Confidence in billing tier")
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)


# Initial festival selection for MVP
INITIAL_FESTIVALS = [
    {
        "id": "lollapalooza",
        "name": "Lollapalooza",
        "city": "Chicago",
        "state": "IL",
        "country": "US",
        "typical_month": 8,
        "typical_duration_days": 4,
        "capacity": 100000,
        "genre_focus": "multi-genre",
        "latitude": 41.8827,
        "longitude": -87.6233,
    },
    {
        "id": "coachella",
        "name": "Coachella Valley Music and Arts Festival",
        "city": "Indio",
        "state": "CA",
        "country": "US",
        "typical_month": 4,
        "typical_duration_days": 6,  # Two weekends
        "capacity": 125000,
        "genre_focus": "multi-genre",
        "latitude": 33.6554,
        "longitude": -116.2167,
    },
    {
        "id": "bonnaroo",
        "name": "Bonnaroo Music and Arts Festival",
        "city": "Manchester",
        "state": "TN",
        "country": "US",
        "typical_month": 6,
        "typical_duration_days": 4,
        "capacity": 80000,
        "genre_focus": "rock_jam",
        "latitude": 35.3328,
        "longitude": -86.0887,
    },
    {
        "id": "outside_lands",
        "name": "Outside Lands Music and Arts Festival",
        "city": "San Francisco",
        "state": "CA",
        "country": "US",
        "typical_month": 8,
        "typical_duration_days": 3,
        "capacity": 75000,
        "genre_focus": "multi-genre",
        "latitude": 37.7694,
        "longitude": -122.4862,
    },
    {
        "id": "austin_city_limits",
        "name": "Austin City Limits Music Festival",
        "city": "Austin",
        "state": "TX",
        "country": "US",
        "typical_month": 10,
        "typical_duration_days": 6,  # Two weekends
        "capacity": 100000,
        "genre_focus": "multi-genre",
        "latitude": 30.2672,
        "longitude": -97.7431,
    },
]


class HistoricalLineupData(BaseModel):
    """Historical lineup data for analysis"""
    festival_id: str = Field(..., description="Festival ID")
    years: List[int] = Field(..., description="Years of data available")
    
    # Statistics
    total_unique_artists: int = Field(default=0, ge=0, description="Total unique artists across all years")
    repeat_artist_rate: float = Field(default=0.0, ge=0, le=1, description="Rate of artist repeats")
    
    # Data quality
    billing_tier_coverage: Dict[str, float] = Field(default_factory=dict, description="Coverage by billing tier")
    missing_billing_tier_count: int = Field(default=0, ge=0, description="Count of entries without billing tier")
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
