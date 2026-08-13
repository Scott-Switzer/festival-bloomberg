"""
Artist factor calculation framework for Festival Bloomberg.

This module provides comprehensive artist factor calculations for booking decisions:
- Momentum: Current cultural velocity and attention
- Relevance: Genre alignment and market fit
- Audience Fit: Demographic and geographic alignment
- Value Proposition: Cost vs impact analysis
- Booking Complexity: Availability, logistics, and negotiation factors
- Risk: Reputation, reliability, and performance risks

All factors are calculated with point-in-time accuracy for backtesting.
"""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime, date
import logging
import statistics

logger = logging.getLogger(__name__)


@dataclass
class ArtistFactors:
    """Comprehensive artist factor scores (0-100 scale)."""
    artist_key: str
    momentum_score: float
    relevance_score: float
    audience_fit_score: float
    value_proposition_score: float
    booking_complexity_score: float
    risk_score: float
    
    # Component breakdowns
    momentum_components: Dict[str, float] = field(default_factory=dict)
    relevance_components: Dict[str, float] = field(default_factory=dict)
    audience_components: Dict[str, float] = field(default_factory=dict)
    value_components: Dict[str, float] = field(default_factory=dict)
    complexity_components: Dict[str, float] = field(default_factory=dict)
    risk_components: Dict[str, float] = field(default_factory=dict)
    
    # Metadata
    calculated_at: datetime = field(default_factory=datetime.utcnow)
    feature_date: Optional[date] = None
    model_version: str = "v1.0"
    confidence: float = 1.0
    
    def overall_score(self) -> float:
        """Calculate weighted overall score."""
        weights = {
            'momentum': 0.25,
            'relevance': 0.20,
            'audience_fit': 0.20,
            'value_proposition': 0.15,
            'booking_complexity': 0.10,
            'risk': 0.10,
        }
        
        return (
            self.momentum_score * weights['momentum'] +
            self.relevance_score * weights['relevance'] +
            self.audience_fit_score * weights['audience_fit'] +
            self.value_proposition_score * weights['value_proposition'] +
            self.booking_complexity_score * weights['booking_complexity'] +
            self.risk_score * weights['risk']
        )


class MomentumCalculator:
    """Calculate artist momentum based on cultural velocity signals."""
    
    def __init__(self, weights: Optional[Dict[str, float]] = None):
        self.weights = weights or {
            'spotify_popularity': 0.30,
            'monthly_listeners': 0.25,
            'social_mentions': 0.20,
            'news_mentions': 0.15,
            'wikipedia_views': 0.10,
        }
    
    def calculate(
        self,
        spotify_popularity: Optional[int] = None,
        monthly_listeners: Optional[int] = None,
        social_mentions: Optional[int] = None,
        news_mentions: Optional[int] = None,
        wikipedia_views: Optional[int] = None,
        historical_trend: Optional[List[float]] = None,
    ) -> float:
        """Calculate momentum score (0-100)."""
        components = {}
        
        # Normalize each component to 0-100 scale
        if spotify_popularity is not None:
            components['spotify_popularity'] = min(spotify_popularity, 100)
        
        if monthly_listeners is not None:
            # Log-scale normalization (1M = 50, 10M = 80, 100M = 95)
            if monthly_listeners > 0:
                log_listeners = (monthly_listeners / 1_000_000)
                components['monthly_listeners'] = min(95, 50 + 30 * (log_listeners / 10))
        
        if social_mentions is not None:
            # Log-scale normalization (10K = 50, 100K = 70, 1M = 85)
            if social_mentions > 0:
                log_mentions = (social_mentions / 10_000)
                components['social_mentions'] = min(85, 50 + 35 * (log_mentions / 100))
        
        if news_mentions is not None:
            # Linear normalization (10 = 50, 100 = 80)
            components['news_mentions'] = min(80, 50 + 30 * (news_mentions / 100))
        
        if wikipedia_views is not None:
            # Log-scale normalization (10K = 50, 100K = 70, 1M = 85)
            if wikipedia_views > 0:
                log_views = (wikipedia_views / 10_000)
                components['wikipedia_views'] = min(85, 50 + 35 * (log_views / 100))
        
        # Calculate weighted average
        weighted_sum = 0.0
        total_weight = 0.0
        
        for component, value in components.items():
            weight = self.weights.get(component, 0)
            weighted_sum += value * weight
            total_weight += weight
        
        if total_weight == 0:
            return 0.0
        
        base_score = weighted_sum / total_weight
        
        # Apply trend adjustment if historical data available
        if historical_trend and len(historical_trend) >= 2:
            recent_trend = (historical_trend[-1] - historical_trend[0]) / historical_trend[0]
            trend_boost = min(20, max(-20, recent_trend * 100))
            base_score = min(100, max(0, base_score + trend_boost))
        
        return round(base_score, 2)


class RelevanceCalculator:
    """Calculate artist relevance to festival context."""
    
    def __init__(self, festival_genres: Optional[List[str]] = None):
        self.festival_genres = festival_genres or []
    
    def calculate(
        self,
        artist_genres: List[str],
        artist_subgenres: List[str],
        festival_genres: Optional[List[str]] = None,
        festival_type: Optional[str] = None,
        decade_popularity: Optional[float] = None,
    ) -> float:
        """Calculate relevance score (0-100)."""
        components = {}
        
        target_genres = festival_genres or self.festival_genres
        
        # Genre alignment
        if target_genres and artist_genres:
            alignment = self._calculate_genre_alignment(artist_genres, target_genres)
            components['genre_alignment'] = alignment * 100
        
        # Subgenre alignment
        if target_genres and artist_subgenres:
            subgenre_alignment = self._calculate_genre_alignment(artist_subgenres, target_genres)
            components['subgenre_alignment'] = subgenre_alignment * 100
        
        # Festival type fit
        if festival_type:
            type_fit = self._calculate_festival_type_fit(artist_genres, festival_type)
            components['festival_type_fit'] = type_fit * 100
        
        # Decade/cultural relevance
        if decade_popularity is not None:
            components['decade_relevance'] = decade_popularity * 100
        
        # Calculate weighted average
        weights = {
            'genre_alignment': 0.40,
            'subgenre_alignment': 0.30,
            'festival_type_fit': 0.20,
            'decade_relevance': 0.10,
        }
        
        weighted_sum = 0.0
        total_weight = 0.0
        
        for component, value in components.items():
            weight = weights.get(component, 0)
            weighted_sum += value * weight
            total_weight += weight
        
        if total_weight == 0:
            return 50.0  # Neutral score if no data
        
        return round(weighted_sum / total_weight, 2)
    
    def _calculate_genre_alignment(self, artist_genres: List[str], target_genres: List[str]) -> float:
        """Calculate genre alignment score (0-1)."""
        artist_genres_lower = [g.lower() for g in artist_genres]
        target_genres_lower = [g.lower() for g in target_genres]
        
        matches = sum(1 for g in artist_genres_lower if any(t in g or g in t for t in target_genres_lower))
        
        if not target_genres_lower:
            return 0.5  # Neutral if no target genres
        
        return min(1.0, matches / len(target_genres_lower))
    
    def _calculate_festival_type_fit(self, artist_genres: List[str], festival_type: str) -> float:
        """Calculate fit for specific festival type."""
        festival_type_lower = festival_type.lower()
        artist_genres_lower = [g.lower() for g in artist_genres]
        
        # Festival type heuristics
        type_preferences = {
            'electronic': ['electronic', 'edm', 'house', 'techno', 'dubstep', 'trance'],
            'rock': ['rock', 'alternative', 'indie', 'punk', 'metal'],
            'pop': ['pop', 'r&b', 'hip hop', 'rap'],
            'jazz': ['jazz', 'blues', 'soul'],
            'folk': ['folk', 'country', 'americana'],
            'classical': ['classical', 'orchestral', 'opera'],
        }
        
        preferred_genres = type_preferences.get(festival_type_lower, [])
        
        if not preferred_genres:
            return 0.5  # Neutral if unknown festival type
        
        matches = sum(1 for g in artist_genres_lower if any(p in g for p in preferred_genres))
        
        return min(1.0, matches / len(preferred_genres))


class AudienceFitCalculator:
    """Calculate audience demographic and geographic fit."""
    
    def calculate(
        self,
        artist_countries: List[str],
        festival_country: str,
        artist_regions: Optional[List[str]] = None,
        festival_region: Optional[str] = None,
        age_demographics: Optional[Dict[str, float]] = None,
        target_demographics: Optional[Dict[str, float]] = None,
    ) -> float:
        """Calculate audience fit score (0-100)."""
        components = {}
        
        # Geographic fit
        if artist_countries and festival_country:
            geo_fit = self._calculate_geographic_fit(artist_countries, festival_country, artist_regions, festival_region)
            components['geographic_fit'] = geo_fit * 100
        
        # Demographic fit
        if age_demographics and target_demographics:
            demo_fit = self._calculate_demographic_fit(age_demographics, target_demographics)
            components['demographic_fit'] = demo_fit * 100
        
        # Calculate weighted average
        weights = {
            'geographic_fit': 0.60,
            'demographic_fit': 0.40,
        }
        
        weighted_sum = 0.0
        total_weight = 0.0
        
        for component, value in components.items():
            weight = weights.get(component, 0)
            weighted_sum += value * weight
            total_weight += weight
        
        if total_weight == 0:
            return 50.0  # Neutral if no data
        
        return round(weighted_sum / total_weight, 2)
    
    def _calculate_geographic_fit(
        self,
        artist_countries: List[str],
        festival_country: str,
        artist_regions: Optional[List[str]] = None,
        festival_region: Optional[str] = None,
    ) -> float:
        """Calculate geographic fit (0-1)."""
        artist_countries_lower = [c.lower() for c in artist_countries]
        festival_country_lower = festival_country.lower()
        
        # Country match
        if festival_country_lower in artist_countries_lower:
            country_score = 1.0
        else:
            # Check for same region/continent
            country_score = 0.5
        
        # Region match if both provided
        if artist_regions and festival_region:
            artist_regions_lower = [r.lower() for r in artist_regions]
            festival_region_lower = festival_region.lower()
            
            if festival_region_lower in artist_regions_lower:
                region_score = 1.0
            else:
                region_score = 0.5
            
            # Average country and region scores
            return (country_score + region_score) / 2
        
        return country_score
    
    def _calculate_demographic_fit(self, artist_demographics: Dict[str, float], target_demographics: Dict[str, float]) -> float:
        """Calculate demographic fit using correlation-like measure."""
        common_keys = set(artist_demographics.keys()) & set(target_demographics.keys())
        
        if not common_keys:
            return 0.5  # Neutral if no overlap
        
        # Calculate alignment
        differences = []
        for key in common_keys:
            artist_val = artist_demographics[key]
            target_val = target_demographics[key]
            difference = abs(artist_val - target_val)
            differences.append(difference)
        
        avg_difference = statistics.mean(differences)
        
        # Convert difference to fit score (smaller difference = higher fit)
        fit_score = max(0.0, 1.0 - avg_difference)
        
        return fit_score


class ValuePropositionCalculator:
    """Calculate value proposition (impact vs cost)."""
    
    def calculate(
        self,
        momentum_score: float,
        relevance_score: float,
        estimated_cost: Optional[float] = None,
        budget_percentage: Optional[float] = None,
        expected_attendance_impact: Optional[float] = None,
        sponsorship_value: Optional[float] = None,
    ) -> float:
        """Calculate value proposition score (0-100)."""
        components = {}
        
        # Impact score (momentum + relevance)
        impact_score = (momentum_score + relevance_score) / 2
        components['impact_score'] = impact_score
        
        # Cost efficiency
        if estimated_cost and budget_percentage:
            cost_efficiency = self._calculate_cost_efficiency(estimated_cost, budget_percentage)
            components['cost_efficiency'] = cost_efficiency * 100
        
        # Attendance impact
        if expected_attendance_impact:
            components['attendance_impact'] = min(100, expected_attendance_impact * 100)
        
        # Sponsorship value
        if sponsorship_value:
            components['sponsorship_value'] = min(100, sponsorship_value * 100)
        
        # Calculate weighted average
        weights = {
            'impact_score': 0.40,
            'cost_efficiency': 0.30,
            'attendance_impact': 0.20,
            'sponsorship_value': 0.10,
        }
        
        weighted_sum = 0.0
        total_weight = 0.0
        
        for component, value in components.items():
            weight = weights.get(component, 0)
            weighted_sum += value * weight
            total_weight += weight
        
        if total_weight == 0:
            return impact_score  # Fall back to impact score
        
        return round(weighted_sum / total_weight, 2)
    
    def _calculate_cost_efficiency(self, estimated_cost: float, budget_percentage: float) -> float:
        """Calculate cost efficiency (0-1)."""
        # Normalize budget percentage (ideal is 5-15% of total budget)
        if budget_percentage < 5:
            efficiency = 0.8  # Very cheap, good efficiency
        elif budget_percentage <= 15:
            efficiency = 1.0  # Ideal range
        elif budget_percentage <= 25:
            efficiency = 0.7  # Acceptable
        else:
            efficiency = 0.4  # Expensive, lower efficiency
        
        return efficiency


class BookingComplexityCalculator:
    """Calculate booking complexity and logistics difficulty."""
    
    def calculate(
        self,
        artist_tier: Optional[str] = None,
        management_availability: Optional[bool] = None,
        tour_conflicts: Optional[int] = None,
        technical_requirements: Optional[List[str]] = None,
        travel_distance: Optional[float] = None,
        visa_requirements: Optional[bool] = None,
    ) -> float:
        """Calculate booking complexity score (0-100, higher = more complex)."""
        components = {}
        
        # Artist tier complexity
        if artist_tier:
            tier_complexity = self._calculate_tier_complexity(artist_tier)
            components['tier_complexity'] = tier_complexity * 100
        
        # Management availability
        if management_availability is not None:
            components['management_availability'] = 0 if management_availability else 80
        
        # Tour conflicts
        if tour_conflicts is not None:
            components['tour_conflicts'] = min(100, tour_conflicts * 20)
        
        # Technical requirements
        if technical_requirements:
            components['technical_requirements'] = min(100, len(technical_requirements) * 10)
        
        # Travel distance
        if travel_distance:
            components['travel_distance'] = min(100, travel_distance / 100)  # 1000km = 10%
        
        # Visa requirements
        if visa_requirements:
            components['visa_requirements'] = 70
        
        # Calculate weighted average
        weights = {
            'tier_complexity': 0.30,
            'management_availability': 0.20,
            'tour_conflicts': 0.20,
            'technical_requirements': 0.15,
            'travel_distance': 0.10,
            'visa_requirements': 0.05,
        }
        
        weighted_sum = 0.0
        total_weight = 0.0
        
        for component, value in components.items():
            weight = weights.get(component, 0)
            weighted_sum += value * weight
            total_weight += weight
        
        if total_weight == 0:
            return 50.0  # Neutral if no data
        
        complexity_score = weighted_sum / total_weight
        
        # Invert: higher complexity = lower score (we want higher score = easier to book)
        ease_score = 100 - complexity_score
        
        return round(ease_score, 2)
    
    def _calculate_tier_complexity(self, artist_tier: str) -> float:
        """Calculate complexity based on artist tier."""
        tier_lower = artist_tier.lower()
        
        complexity_map = {
            'headliner': 0.9,
            'sub_headliner': 0.7,
            'supporting': 0.5,
            'early_day': 0.3,
            'dj_only': 0.2,
        }
        
        return complexity_map.get(tier_lower, 0.5)


class RiskCalculator:
    """Calculate booking and performance risk."""
    
    def calculate(
        self,
        cancellation_history: Optional[float] = None,
        reliability_score: Optional[float] = None,
        controversy_risk: Optional[float] = None,
        health_issues: Optional[bool] = None,
        contract_disputes: Optional[int] = None,
        age_risk: Optional[float] = None,
    ) -> float:
        """Calculate risk score (0-100, higher = riskier)."""
        components = {}
        
        # Cancellation history
        if cancellation_history is not None:
            components['cancellation_history'] = cancellation_history * 100
        
        # Reliability score (invert: lower reliability = higher risk)
        if reliability_score is not None:
            components['reliability_risk'] = (1 - reliability_score) * 100
        
        # Controversy risk
        if controversy_risk is not None:
            components['controversy_risk'] = controversy_risk * 100
        
        # Health issues
        if health_issues:
            components['health_risk'] = 70
        
        # Contract disputes
        if contract_disputes is not None:
            components['contract_disputes'] = min(100, contract_disputes * 25)
        
        # Age risk (very young or very old artists)
        if age_risk is not None:
            components['age_risk'] = age_risk * 100
        
        # Calculate weighted average
        weights = {
            'cancellation_history': 0.25,
            'reliability_risk': 0.25,
            'controversy_risk': 0.20,
            'health_risk': 0.15,
            'contract_disputes': 0.10,
            'age_risk': 0.05,
        }
        
        weighted_sum = 0.0
        total_weight = 0.0
        
        for component, value in components.items():
            weight = weights.get(component, 0)
            weighted_sum += value * weight
            total_weight += weight
        
        if total_weight == 0:
            return 25.0  # Low default risk
        
        risk_score = weighted_sum / total_weight
        
        # Invert: higher risk = lower score (we want higher score = lower risk)
        safety_score = 100 - risk_score
        
        return round(safety_score, 2)


class ArtistFactorCalculator:
    """Main calculator for comprehensive artist factors."""
    
    def __init__(self, festival_context: Optional[Dict[str, Any]] = None):
        self.festival_context = festival_context or {}
        
        self.momentum_calc = MomentumCalculator()
        self.relevance_calc = RelevanceCalculator(festival_context.get('genres'))
        self.audience_calc = AudienceFitCalculator()
        self.value_calc = ValuePropositionCalculator()
        self.complexity_calc = BookingComplexityCalculator()
        self.risk_calc = RiskCalculator()
    
    def calculate_factors(
        self,
        artist_key: str,
        artist_data: Dict[str, Any],
        feature_date: Optional[date] = None,
    ) -> ArtistFactors:
        """Calculate all factors for an artist."""
        
        # Extract artist data
        genres = artist_data.get('genres', [])
        subgenres = artist_data.get('subgenres', [])
        countries = artist_data.get('countries', [artist_data.get('country', '')])
        regions = artist_data.get('regions', [artist_data.get('origin_region', '')])
        
        # Calculate momentum
        momentum_score = self.momentum_calc.calculate(
            spotify_popularity=artist_data.get('spotify_popularity'),
            monthly_listeners=artist_data.get('monthly_listeners'),
            social_mentions=artist_data.get('social_mentions'),
            news_mentions=artist_data.get('news_mentions'),
            wikipedia_views=artist_data.get('wikipedia_views'),
            historical_trend=artist_data.get('historical_trend'),
        )
        
        # Calculate relevance
        relevance_score = self.relevance_calc.calculate(
            artist_genres=genres,
            artist_subgenres=subgenres,
            festival_genres=self.festival_context.get('genres'),
            festival_type=self.festival_context.get('festival_type'),
            decade_popularity=artist_data.get('decade_popularity'),
        )
        
        # Calculate audience fit
        audience_score = self.audience_calc.calculate(
            artist_countries=countries,
            festival_country=self.festival_context.get('country', ''),
            artist_regions=regions,
            festival_region=self.festival_context.get('region'),
            age_demographics=artist_data.get('age_demographics'),
            target_demographics=self.festival_context.get('target_demographics'),
        )
        
        # Calculate value proposition
        value_score = self.value_calc.calculate(
            momentum_score=momentum_score,
            relevance_score=relevance_score,
            estimated_cost=artist_data.get('estimated_cost'),
            budget_percentage=artist_data.get('budget_percentage'),
            expected_attendance_impact=artist_data.get('attendance_impact'),
            sponsorship_value=artist_data.get('sponsorship_value'),
        )
        
        # Calculate booking complexity
        complexity_score = self.complexity_calc.calculate(
            artist_tier=artist_data.get('tier'),
            management_availability=artist_data.get('management_available'),
            tour_conflicts=artist_data.get('tour_conflicts'),
            technical_requirements=artist_data.get('technical_requirements'),
            travel_distance=artist_data.get('travel_distance'),
            visa_requirements=artist_data.get('visa_required'),
        )
        
        # Calculate risk
        risk_score = self.risk_calc.calculate(
            cancellation_history=artist_data.get('cancellation_rate'),
            reliability_score=artist_data.get('reliability_score'),
            controversy_risk=artist_data.get('controversy_risk'),
            health_issues=artist_data.get('health_issues'),
            contract_disputes=artist_data.get('contract_disputes'),
            age_risk=artist_data.get('age_risk'),
        )
        
        return ArtistFactors(
            artist_key=artist_key,
            momentum_score=momentum_score,
            relevance_score=relevance_score,
            audience_fit_score=audience_score,
            value_proposition_score=value_score,
            booking_complexity_score=complexity_score,
            risk_score=risk_score,
            feature_date=feature_date,
        )