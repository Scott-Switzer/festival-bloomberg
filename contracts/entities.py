"""
Canonical entity contracts for Festival Intelligence Terminal.
Defines the core data structures for artists, festivals, venues, and events.
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, field_validator
from enum import Enum


class DataSource(str, Enum):
    """Data source enumeration"""
    MUSICBRAINZ = "musicbrainz"
    WIKIDATA = "wikidata"
    SETLISTFM = "setlistfm"
    TICKETMASTER = "ticketmaster"
    YOUTUBE = "youtube"
    WIKIMEDIA = "wikimedia"
    GDELT = "gdelt"
    NWS = "nws"
    NOAA_NCEI = "noaa_ncei"
    BTS = "bts"
    CENSUS = "census"
    BEA = "bea"
    BLS = "bls"
    OPENSTREETMAP = "openstreetmap"


class MetricType(str, Enum):
    """Type of metric classification"""
    OBSERVED = "observed"  # Directly measured from public sources
    MODELED = "modeled"    # Statistically derived predictions
    ASSUMPTION = "assumption"  # User-selected inputs
    PRIVATE = "private"    # Private data fields (unavailable in public demo)


class BillingTier(str, Enum):
    """Festival billing tiers"""
    HEADLINER = "headliner"
    SUB_HEADLINER = "sub_headliner"
    MAIN_STAGE = "main_stage"
    SECONDARY = "secondary"
    EMERGING = "emerging"
    UNKNOWN = "unknown"


class Artist(BaseModel):
    """Canonical artist entity"""
    musicbrainz_id: str = Field(..., description="MusicBrainz artist ID (primary key)")
    wikidata_id: Optional[str] = Field(None, description="Wikidata QID")
    ticketmaster_id: Optional[str] = Field(None, description="Ticketmaster attraction ID")
    youtube_channel_id: Optional[str] = Field(None, description="YouTube channel ID")
    spotify_id: Optional[str] = Field(None, description="Spotify artist ID")
    setlistfm_id: Optional[str] = Field(None, description="setlist.fm artist ID")
    
    normalized_name: str = Field(..., description="Normalized artist name")
    aliases: List[str] = Field(default_factory=list, description="Alternative names")
    
    # Metadata
    country: Optional[str] = Field(None, description="Artist country of origin")
    genre: Optional[str] = Field(None, description="Primary genre")
    genres: List[str] = Field(default_factory=list, description="All genres")
    formed_year: Optional[int] = Field(None, description="Year artist was formed")
    disband_year: Optional[int] = Field(None, description="Year artist disbanded")
    
    # Entity resolution
    name_confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Confidence in name resolution")
    manually_reviewed: bool = Field(default=False, description="Whether entity resolution was manually reviewed")
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Festival(BaseModel):
    """Canonical festival entity"""
    id: str = Field(..., description="Internal festival ID")
    name: str = Field(..., description="Festival name")
    normalized_name: str = Field(..., description="Normalized festival name")
    
    # Location
    city: str = Field(..., description="Festival city")
    state: Optional[str] = Field(None, description="Festival state/province")
    country: str = Field(default="US", description="Festival country")
    latitude: Optional[float] = Field(None, ge=-90, le=90, description="Venue latitude")
    longitude: Optional[float] = Field(None, ge=-180, le=180, description="Venue longitude")
    
    # Event details
    typical_month: Optional[int] = Field(None, ge=1, le=12, description="Typical festival month")
    typical_duration_days: Optional[int] = Field(None, ge=1, description="Typical festival duration")
    capacity: Optional[int] = Field(None, ge=0, description="Estimated capacity")
    
    # Classification
    genre_focus: Optional[str] = Field(None, description="Primary genre focus")
    festival_type: Optional[str] = Field(None, description="Festival type (e.g., music, arts)")
    
    # External IDs
    ticketmaster_id: Optional[str] = Field(None, description="Ticketmaster event ID")
    wikidata_id: Optional[str] = Field(None, description="Wikidata QID")
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Venue(BaseModel):
    """Canonical venue entity"""
    id: str = Field(..., description="Internal venue ID")
    name: str = Field(..., description="Venue name")
    normalized_name: str = Field(..., description="Normalized venue name")
    
    # Location
    city: str = Field(..., description="Venue city")
    state: Optional[str] = Field(None, description="Venue state/province")
    country: str = Field(default="US", description="Venue country")
    latitude: Optional[float] = Field(None, ge=-90, le=90, description="Venue latitude")
    longitude: Optional[float] = Field(None, ge=-180, le=180, description="Venue longitude")
    
    # Capacity
    capacity: Optional[int] = Field(None, ge=0, description="Venue capacity")
    
    # External IDs
    ticketmaster_id: Optional[str] = Field(None, description="Ticketmaster venue ID")
    musicbrainz_id: Optional[str] = Field(None, description="MusicBrainz venue ID")
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Event(BaseModel):
    """Canonical event entity (concert, festival appearance, etc.)"""
    id: str = Field(..., description="Internal event ID")
    artist_id: str = Field(..., description="Artist ID (references Artist.musicbrainz_id)")
    venue_id: Optional[str] = Field(None, description="Venue ID")
    festival_id: Optional[str] = Field(None, description="Festival ID if festival appearance")
    
    # Event details
    event_date: datetime = Field(..., description="Event date and time")
    event_type: str = Field(..., description="Event type (concert, festival, etc.)")
    
    # Festival-specific
    billing_tier: Optional[BillingTier] = Field(None, description="Festival billing tier")
    day_of_festival: Optional[int] = Field(None, ge=1, description="Day of multi-day festival")
    
    # External IDs
    ticketmaster_id: Optional[str] = Field(None, description="Ticketmaster event ID")
    setlistfm_id: Optional[str] = Field(None, description="setlist.fm event ID")
    
    # Data provenance
    source: DataSource = Field(..., description="Data source")
    retrieved_at: datetime = Field(default_factory=datetime.utcnow, description="When data was retrieved")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Data confidence score")
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ArtistMomentum(BaseModel):
    """Artist momentum metrics"""
    artist_id: str = Field(..., description="Artist ID")
    observation_date: datetime = Field(..., description="Observation date")
    
    # Momentum scores
    momentum_score: float = Field(..., ge=0, le=100, description="Overall momentum score (0-100)")
    momentum_percentile: float = Field(..., ge=0, le=100, description="Momentum percentile among artists")
    
    # Component scores
    youtube_momentum: Optional[float] = Field(None, ge=0, le=100, description="YouTube-based momentum")
    wiki_momentum: Optional[float] = Field(None, ge=0, le=100, description="Wikipedia-based momentum")
    news_momentum: Optional[float] = Field(None, ge=0, le=100, description="News-based momentum")
    
    # Trends
    momentum_change_30d: Optional[float] = Field(None, description="30-day momentum change")
    momentum_change_90d: Optional[float] = Field(None, description="90-day momentum change")
    
    # Data provenance
    model_version: str = Field(..., description="Model version used")
    feature_version: str = Field(..., description="Feature version used")
    metric_type: MetricType = Field(default=MetricType.MODELED, description="Type of metric")
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)


class BookingValueIndex(BaseModel):
    """Artist booking value index (0-100 percentile)"""
    artist_id: str = Field(..., description="Artist ID")
    observation_date: datetime = Field(..., description="Observation date")
    
    # Value index
    booking_value_index: float = Field(..., ge=0, le=100, description="Booking value index (0-100)")
    
    # Predictions
    predicted_billing_tier: Optional[BillingTier] = Field(None, description="Predicted billing tier")
    predicted_festival_demand_rank: Optional[int] = Field(None, ge=1, description="Predicted festival demand rank")
    
    # Observed
    observed_recent_billing_tier: Optional[BillingTier] = Field(None, description="Most recent observed billing tier")
    
    # Residual (opportunity detection)
    momentum_to_billing_residual: Optional[float] = Field(None, description="Momentum minus billing (opportunity score)")
    
    # Component scores
    youtube_growth_score: Optional[float] = Field(None, ge=0, le=100)
    wiki_growth_score: Optional[float] = Field(None, ge=0, le=100)
    news_volume_score: Optional[float] = Field(None, ge=0, le=100)
    live_performance_frequency: Optional[float] = Field(None, ge=0, le=100)
    venue_progression_score: Optional[float] = Field(None, ge=0, le=100)
    festival_billing_history_score: Optional[float] = Field(None, ge=0, le=100)
    headliner_frequency_score: Optional[float] = Field(None, ge=0, le=100)
    market_diversity_score: Optional[float] = Field(None, ge=0, le=100)
    release_recency_score: Optional[float] = Field(None, ge=0, le=100)
    genre_momentum_score: Optional[float] = Field(None, ge=0, le=100)
    competition_score: Optional[float] = Field(None, ge=0, le=100)
    local_affinity_score: Optional[float] = Field(None, ge=0, le=100)
    
    # Data provenance
    model_version: str = Field(..., description="Model version")
    feature_version: str = Field(..., description="Feature version")
    metric_type: MetricType = Field(default=MetricType.MODELED, description="Type of metric")
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)


class TourPrediction(BaseModel):
    """Tour/festival appearance prediction"""
    artist_id: str = Field(..., description="Artist ID")
    prediction_date: datetime = Field(..., description="When prediction was made")
    
    # Predictions
    tour_probability_90d: float = Field(..., ge=0, le=1, description="Probability of tour in next 90 days")
    tour_probability_180d: float = Field(..., ge=0, le=1, description="Probability of tour in next 180 days")
    tour_probability_365d: float = Field(..., ge=0, le=1, description="Probability of tour in next 365 days")
    
    festival_appearance_probability: Optional[float] = Field(None, ge=0, le=1, description="Probability of festival appearance")
    market_appearance_probability: Optional[float] = Field(None, ge=0, le=1, description="Probability of appearance in specific market")
    
    # Routing feasibility
    geographically_routable: Optional[bool] = Field(None, description="Whether artist is geographically routable")
    routing_confidence: Optional[float] = Field(None, ge=0, le=1, description="Confidence in routing assessment")
    
    # Data provenance
    model_version: str = Field(..., description="Model version")
    feature_version: str = Field(..., description="Feature version")
    metric_type: MetricType = Field(default=MetricType.MODELED, description="Type of metric")
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)


class FestivalComparison(BaseModel):
    """Festival comparison metrics"""
    festival_id: str = Field(..., description="Festival ID")
    comparison_date: datetime = Field(..., description="Comparison date")
    
    # Lineup metrics
    lineup_strength_index: float = Field(..., ge=0, le=100, description="Overall lineup strength")
    headliner_dependency: float = Field(..., ge=0, le=1, description="Dependency on headliners")
    genre_entropy: float = Field(..., ge=0, description="Genre diversity (higher = more diverse)")
    emerging_artist_share: float = Field(..., ge=0, le=1, description="Share of emerging artists")
    lineup_uniqueness: float = Field(..., ge=0, le=1, description="How unique the lineup is")
    competitive_overlap: Optional[float] = Field(None, ge=0, le=1, description="Overlap with competing festivals")
    average_artist_momentum: Optional[float] = Field(None, ge=0, le=100, description="Average artist momentum")
    market_fit_score: Optional[float] = Field(None, ge=0, le=100, description="Fit with local market")
    
    # Data provenance
    model_version: str = Field(..., description="Model version")
    feature_version: str = Field(..., description="Feature version")
    metric_type: MetricType = Field(default=MetricType.MODELED, description="Type of metric")
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)


class RevenueScenario(BaseModel):
    """Revenue scenario model"""
    scenario_id: str = Field(..., description="Scenario ID")
    festival_id: str = Field(..., description="Festival ID")
    scenario_date: datetime = Field(..., description="Scenario creation date")
    
    # User inputs (assumptions)
    capacity: int = Field(..., ge=0, description="Festival capacity")
    expected_attendance: int = Field(..., ge=0, description="Expected attendance")
    ticket_tiers: Dict[str, Any] = Field(..., description="Ticket tiers and prices")
    vip_mix: float = Field(default=0.0, ge=0, le=1, description="VIP ticket mix")
    sponsorship_commitments: float = Field(default=0.0, ge=0, description="Sponsorship commitments")
    per_capita_fnb_spending: float = Field(default=0.0, ge=0, description="Per-capita food & beverage spending")
    per_capita_merch_spending: float = Field(default=0.0, ge=0, description="Per-capita merchandise spending")
    artist_cost_range: tuple[float, float] = Field(..., description="Artist cost range (min, max)")
    production_costs: float = Field(default=0.0, ge=0, description="Production costs")
    weather_assumption: Optional[str] = Field(None, description="Weather assumption")
    
    # Calculated outputs
    ticket_revenue: float = Field(..., ge=0, description="Ticket revenue")
    ancillary_revenue: float = Field(..., ge=0, description="Ancillary revenue")
    total_revenue: float = Field(..., ge=0, description="Total revenue")
    artist_costs: float = Field(..., ge=0, description="Artist costs")
    contribution_margin: float = Field(..., description="Contribution margin")
    
    # Monte Carlo results
    p10_downside: Optional[float] = Field(None, description="P10 downside revenue")
    p50_base_case: Optional[float] = Field(None, description="P50 base case revenue")
    p90_upside: Optional[float] = Field(None, description="P90 upside revenue")
    profitability_probability: Optional[float] = Field(None, ge=0, le=1, description="Probability of profitability")
    break_even_attendance: Optional[int] = Field(None, ge=0, description="Break-even attendance")
    break_even_ticket_price: Optional[float] = Field(None, ge=0, description="Break-even ticket price")
    
    # Sensitivity
    revenue_at_risk_weather: Optional[float] = Field(None, description="Revenue at risk from weather")
    artist_sensitivity: Optional[Dict[str, float]] = Field(None, description="Revenue sensitivity to each artist")
    
    # Data provenance
    model_version: str = Field(..., description="Model version")
    metric_type: MetricType = Field(default=MetricType.ASSUMPTION, description="Type of metric")
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)


class LocationIntelligence(BaseModel):
    """Location and market intelligence"""
    festival_id: str = Field(..., description="Festival ID")
    observation_date: datetime = Field(..., description="Observation date")
    
    # Weather risk
    weather_risk_score: Optional[float] = Field(None, ge=0, le=100, description="Weather risk score")
    heat_stress_score: Optional[float] = Field(None, ge=0, le=100, description="Heat stress score")
    rain_disruption_probability: Optional[float] = Field(None, ge=0, le=1, description="Rain disruption probability")
    expected_weather_adjusted_attendance: Optional[float] = Field(None, description="Weather-adjusted attendance")
    
    # Air access
    air_access_score: Optional[float] = Field(None, ge=0, le=100, description="Air access score")
    weighted_avg_origin_distance: Optional[float] = Field(None, ge=0, description="Weighted average origin distance")
    direct_flight_coverage: Optional[float] = Field(None, ge=0, le=1, description="Direct flight coverage")
    historical_passenger_capacity: Optional[int] = Field(None, ge=0, description="Historical passenger capacity")
    travel_cost_index: Optional[float] = Field(None, ge=0, description="Travel cost index")
    
    # Hotel pressure
    hotel_pressure_proxy: Optional[float] = Field(None, ge=0, description="Hotel pressure proxy")
    estimated_overnight_visitors: Optional[int] = Field(None, ge=0, description="Estimated overnight visitors")
    estimated_room_supply: Optional[int] = Field(None, ge=0, description="Estimated room supply")
    
    # Market demographics
    market_population: Optional[int] = Field(None, ge=0, description="Market population")
    median_income: Optional[float] = Field(None, ge=0, description="Median income")
    age_distribution: Optional[Dict[str, float]] = Field(None, description="Age distribution")
    
    # Data provenance
    model_version: str = Field(..., description="Model version")
    feature_version: str = Field(..., description="Feature version")
    metric_type: MetricType = Field(default=MetricType.MODELED, description="Type of metric")
    
    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)


class DataProvenance(BaseModel):
    """Standard data provenance fields for all records"""
    source: DataSource = Field(..., description="Data source")
    retrieved_at: datetime = Field(default_factory=datetime.utcnow, description="When data was retrieved")
    observation_date: Optional[datetime] = Field(None, description="Date of the observation")
    model_version: Optional[str] = Field(None, description="Model version if applicable")
    feature_version: Optional[str] = Field(None, description="Feature version if applicable")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Confidence score")
    is_observed: bool = Field(default=True, description="Whether metric is observed")
    is_estimated: bool = Field(default=False, description="Whether metric is estimated")
    is_synthetic: bool = Field(default=False, description="Whether metric is synthetic")
