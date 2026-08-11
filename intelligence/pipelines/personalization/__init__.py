"""
Festival-specific personalization engine.
Adapts insights to specific festival characteristics (climate, region, size, pricing).
"""
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta
import statistics
from collections import defaultdict


@dataclass
class FestivalCharacteristics:
    """Complete festival profile for personalization."""
    festival_id: str
    basic_info: Dict[str, Any]
    environmental_factors: Dict[str, Any]
    regional_context: Dict[str, Any]
    historical_patterns: Dict[str, Any]


@dataclass
class ArtistFitAnalysis:
    """Analysis of artist fit for specific festival."""
    artist_id: str
    artist_name: str
    fit_score: float
    fit_analysis: Dict[str, Any]
    recommendation: str
    pricing_recommendation: Dict[str, Any]


class FestivalPersonalizationEngine:
    """Personalize insights for specific festival characteristics."""
    
    def __init__(self):
        self.festival_database = FestivalCharacteristicsDatabase()
        self.climate_analyzer = ClimateAnalyzer()
        self.regional_analyzer = RegionalAnalyzer()
        self.demographic_analyzer = DemographicAnalyzer()
        self.economic_analyzer = EconomicAnalyzer()
        self.genre_analyzer = GenreAnalyzer()
    
    def analyze_festival_characteristics(self, festival_id: str) -> FestivalCharacteristics:
        """
        Complete festival profile for personalization.
        
        Args:
            festival_id: Festival identifier
            
        Returns:
            Complete festival characteristics
        """
        festival_data = self.festival_database.get_festival(festival_id)
        
        return FestivalCharacteristics(
            festival_id=festival_id,
            basic_info=self._analyze_basic_info(festival_data),
            environmental_factors=self._analyze_environmental_factors(festival_data),
            regional_context=self._analyze_regional_context(festival_data),
            historical_patterns=self._analyze_historical_patterns(festival_id)
        )
    
    def _analyze_basic_info(self, festival_data: Dict) -> Dict[str, Any]:
        """Analyze basic festival information."""
        return {
            'capacity': festival_data.get('capacity', 0),
            'genre_focus': festival_data.get('genre_focus', []),
            'location': festival_data.get('location', {}),
            'dates': festival_data.get('dates', []),
            'pricing_tiers': festival_data.get('pricing_tiers', {}),
            'venue_type': festival_data.get('venue_type', 'outdoor'),
            'duration_days': festival_data.get('duration_days', 3)
        }
    
    def _analyze_environmental_factors(self, festival_data: Dict) -> Dict[str, Any]:
        """Analyze environmental factors."""
        location = festival_data.get('location', {})
        dates = festival_data.get('dates', [])
        
        return {
            'climate_patterns': self.climate_analyzer.analyze_historical(location),
            'weather_risks': self.climate_analyzer.assess_risks(location, dates),
            'seasonal_considerations': self.climate_analyzer.seasonal_analysis(dates),
            'optimal_conditions': self.climate_analyzer.identify_optimal_conditions(location, dates)
        }
    
    def _analyze_regional_context(self, festival_data: Dict) -> Dict[str, Any]:
        """Analyze regional context."""
        location = festival_data.get('location', {})
        
        return {
            'music_market': self.regional_analyzer.music_market(location),
            'competition': self.regional_analyzer.competitive_landscape(location),
            'transportation': self.regional_analyzer.transportation_access(location),
            'accommodation': self.regional_analyzer.accommodation_capacity(location),
            'economic_context': self.economic_analyzer.analyze(location)
        }
    
    def _analyze_historical_patterns(self, festival_id: str) -> Dict[str, Any]:
        """Analyze historical patterns for festival."""
        return {
            'booking_patterns': self._analyze_booking_patterns(festival_id),
            'audience_preferences': self._analyze_audience_preferences(festival_id),
            'success_factors': self._identify_success_factors(festival_id),
            'genre_performance': self._analyze_genre_performance(festival_id)
        }
    
    def personalize_artist_recommendations(self, festival_id: str, artist_candidates: List[Dict]) -> List[ArtistFitAnalysis]:
        """
        Personalize recommendations for specific festival.
        
        Args:
            festival_id: Festival identifier
            artist_candidates: List of candidate artists
            
        Returns:
            Ranked list of personalized artist recommendations
        """
        festival_profile = self.analyze_festival_characteristics(festival_id)
        
        scored_candidates = []
        for artist in artist_candidates:
            artist_profile = self._get_artist_profile(artist['id'])
            
            fit_score = self._calculate_fit_score(artist_profile, festival_profile)
            
            fit_analysis = {
                'genre_fit': self._calculate_genre_fit(artist_profile, festival_profile),
                'demographic_fit': self._calculate_demographic_fit(artist_profile, festival_profile),
                'climate_suitability': self._assess_climate_suitability(artist_profile, festival_profile),
                'regional_appeal': self._assess_regional_appeal(artist_profile, festival_profile),
                'economic_alignment': self._assess_economic_alignment(artist_profile, festival_profile),
                'historical_success': self._assess_historical_success(artist_profile, festival_profile),
                'stage_suitability': self._assess_stage_suitability(artist_profile, festival_profile),
                'timing_fit': self._assess_timing_fit(artist_profile, festival_profile)
            }
            
            recommendation = self._generate_recommendation(artist_profile, festival_profile, fit_score)
            pricing_recommendation = self._personalize_pricing(artist_profile, festival_profile)
            
            scored_candidates.append(ArtistFitAnalysis(
                artist_id=artist['id'],
                artist_name=artist['name'],
                fit_score=fit_score,
                fit_analysis=fit_analysis,
                recommendation=recommendation,
                pricing_recommendation=pricing_recommendation
            ))
        
        return sorted(scored_candidates, key=lambda x: x.fit_score, reverse=True)
    
    def _calculate_fit_score(self, artist_profile: Dict, festival_profile: FestivalCharacteristics) -> float:
        """Calculate overall fit score for artist at festival."""
        weights = {
            'genre_fit': 0.25,
            'demographic_fit': 0.20,
            'regional_appeal': 0.20,
            'economic_alignment': 0.15,
            'historical_success': 0.10,
            'climate_suitability': 0.10
        }
        
        scores = {
            'genre_fit': self._calculate_genre_fit(artist_profile, festival_profile),
            'demographic_fit': self._calculate_demographic_fit(artist_profile, festival_profile),
            'regional_appeal': self._assess_regional_appeal(artist_profile, festival_profile),
            'economic_alignment': self._assess_economic_alignment(artist_profile, festival_profile),
            'historical_success': self._assess_historical_success(artist_profile, festival_profile),
            'climate_suitability': self._assess_climate_suitability(artist_profile, festival_profile)
        }
        
        weighted_score = sum(scores[k] * weights[k] for k in scores)
        
        return min(1.0, max(0.0, weighted_score))
    
    def _calculate_genre_fit(self, artist_profile: Dict, festival_profile: FestivalCharacteristics) -> float:
        """Calculate genre fit between artist and festival."""
        artist_genres = set(artist_profile.get('genres', []))
        festival_genres = set(festival_profile.basic_info.get('genre_focus', []))
        
        if not festival_genres:
            return 0.5  # Neutral if no genre focus specified
        
        # Calculate overlap
        overlap = artist_genres & festival_genres
        union = artist_genres | festival_genres
        
        if not union:
            return 0.0
        
        genre_similarity = len(overlap) / len(union)
        
        # Boost for exact matches
        if artist_genres == festival_genres:
            genre_similarity = min(1.0, genre_similarity + 0.2)
        
        return genre_similarity
    
    def _calculate_demographic_fit(self, artist_profile: Dict, festival_profile: FestivalCharacteristics) -> float:
        """Calculate demographic fit between artist and festival."""
        artist_demographics = artist_profile.get('audience_demographics', {})
        festival_demographics = self.demographic_analyzer.analyze(
            festival_profile.basic_info.get('location', {})
        )
        
        if not artist_demographics or not festival_demographics:
            return 0.5
        
        # Compare key demographic factors
        age_match = self._compare_demographic_range(
            artist_demographics.get('age_range'),
            festival_demographics.get('age_range')
        )
        
        income_match = self._compare_demographic_range(
            artist_demographics.get('income_range'),
            festival_demographics.get('income_range')
        )
        
        return (age_match + income_match) / 2
    
    def _assess_climate_suitability(self, artist_profile: Dict, festival_profile: FestivalCharacteristics) -> float:
        """Assess climate suitability for artist performance."""
        artist_preferences = artist_profile.get('performance_preferences', {})
        festival_climate = festival_profile.environmental_factors.get('climate_patterns', {})
        
        if not artist_preferences or not festival_climate:
            return 0.5
        
        # Check if artist prefers indoor/outdoor
        artist_venue_preference = artist_preferences.get('venue_type')
        festival_venue_type = festival_profile.basic_info.get('venue_type')
        
        if artist_venue_preference and festival_venue_type:
            if artist_venue_preference == festival_venue_type:
                venue_score = 1.0
            else:
                venue_score = 0.3
        else:
            venue_score = 0.5
        
        # Check temperature preferences
        artist_temp_range = artist_preferences.get('temperature_range')
        festival_temp = festival_climate.get('average_temperature')
        
        if artist_temp_range and festival_temp:
            if artist_temp_range[0] <= festival_temp <= artist_temp_range[1]:
                temp_score = 1.0
            else:
                temp_score = 0.5
        else:
            temp_score = 0.5
        
        return (venue_score + temp_score) / 2
    
    def _assess_regional_appeal(self, artist_profile: Dict, festival_profile: FestivalCharacteristics) -> float:
        """Assess artist's regional appeal."""
        artist_regions = artist_profile.get('strong_regions', [])
        festival_region = festival_profile.basic_info.get('location', {}).get('region')
        
        if not artist_regions or not festival_region:
            return 0.5
        
        # Check if festival region is in artist's strong regions
        if festival_region in artist_regions:
            return 0.9
        elif any(region in festival_region for region in artist_regions):
            return 0.7
        else:
            return 0.4
    
    def _assess_economic_alignment(self, artist_profile: Dict, festival_profile: FestivalCharacteristics) -> float:
        """Assess economic alignment between artist and festival."""
        artist_pricing = artist_profile.get('pricing_tier', 'mid')
        festival_budget = festival_profile.regional_context.get('economic_context', {}).get('budget_tier', 'mid')
        
        pricing_tiers = ['low', 'mid', 'high', 'premium']
        
        artist_tier_index = pricing_tiers.index(artist_pricing) if artist_pricing in pricing_tiers else 1
        festival_tier_index = pricing_tiers.index(festival_budget) if festival_budget in pricing_tiers else 1
        
        # Calculate alignment (closer tiers = better alignment)
        tier_difference = abs(artist_tier_index - festival_tier_index)
        
        if tier_difference == 0:
            return 1.0
        elif tier_difference == 1:
            return 0.7
        elif tier_difference == 2:
            return 0.4
        else:
            return 0.2
    
    def _assess_historical_success(self, artist_profile: Dict, festival_profile: FestivalCharacteristics) -> float:
        """Assess artist's historical success at similar festivals."""
        artist_festival_history = artist_profile.get('festival_history', [])
        festival_type = festival_profile.basic_info.get('festival_type')
        
        if not artist_festival_history:
            return 0.5
        
        # Find similar festival appearances
        similar_appearances = [
            appearance for appearance in artist_festival_history
            if appearance.get('festival_type') == festival_type
        ]
        
        if not similar_appearances:
            return 0.5
        
        # Calculate average performance score
        avg_performance = statistics.mean(
            [appearance.get('performance_score', 0.5) for appearance in similar_appearances]
        )
        
        return avg_performance
    
    def _assess_stage_suitability(self, artist_profile: Dict, festival_profile: FestivalCharacteristics) -> float:
        """Assess suitability for festival stages."""
        artist_stage_requirements = artist_profile.get('stage_requirements', {})
        festival_stages = festival_profile.basic_info.get('available_stages', [])
        
        if not artist_stage_requirements or not festival_stages:
            return 0.5
        
        # Check if festival has required stage capabilities
        required_capabilities = artist_stage_requirements.get('required_capabilities', [])
        
        available_capabilities = set()
        for stage in festival_stages:
            available_capabilities.update(stage.get('capabilities', []))
        
        if not required_capabilities:
            return 0.5
        
        # Calculate capability match
        matched_capabilities = set(required_capabilities) & available_capabilities
        capability_score = len(matched_capabilities) / len(required_capabilities)
        
        return capability_score
    
    def _assess_timing_fit(self, artist_profile: Dict, festival_profile: FestivalCharacteristics) -> float:
        """Assess timing fit for artist availability."""
        artist_availability = artist_profile.get('availability_windows', [])
        festival_dates = festival_profile.basic_info.get('dates', [])
        
        if not artist_availability or not festival_dates:
            return 0.5
        
        # Check if festival dates fall within artist availability windows
        for window in artist_availability:
            window_start = datetime.strptime(window['start'], '%Y-%m-%d')
            window_end = datetime.strptime(window['end'], '%Y-%m-%d')
            
            for festival_date in festival_dates:
                festival_dt = datetime.strptime(festival_date, '%Y-%m-%d')
                
                if window_start <= festival_dt <= window_end:
                    return 0.9
        
        return 0.3
    
    def _generate_recommendation(self, artist_profile: Dict, festival_profile: FestivalCharacteristics, fit_score: float) -> str:
        """Generate recommendation based on fit score."""
        if fit_score > 0.85:
            return 'EXCELLENT FIT - Strongly recommend for booking'
        elif fit_score > 0.70:
            return 'GOOD FIT - Recommend for booking'
        elif fit_score > 0.55:
            return 'MODERATE FIT - Consider with specific conditions'
        elif fit_score > 0.40:
            return 'POOR FIT - Not recommended unless special circumstances'
        else:
            return 'VERY POOR FIT - Do not recommend'
    
    def _personalize_pricing(self, artist_profile: Dict, festival_profile: FestivalCharacteristics) -> Dict[str, Any]:
        """Personalize pricing recommendation for artist at festival."""
        base_pricing = artist_profile.get('base_pricing_range', {'min': 10000, 'max': 50000})
        
        # Adjust based on festival characteristics
        festival_multiplier = self._calculate_festival_pricing_multiplier(festival_profile)
        artist_demand_multiplier = self._calculate_artist_demand_multiplier(artist_profile)
        
        adjusted_min = base_pricing['min'] * festival_multiplier * artist_demand_multiplier
        adjusted_max = base_pricing['max'] * festival_multiplier * artist_demand_multiplier
        
        return {
            'recommended_range': {
                'min': int(adjusted_min),
                'max': int(adjusted_max)
            },
            'pricing_factors': {
                'base_pricing': base_pricing,
                'festival_multiplier': festival_multiplier,
                'artist_demand_multiplier': artist_demand_multiplier,
                'regional_adjustment': self._calculate_regional_pricing_adjustment(artist_profile, festival_profile),
                'timing_adjustment': self._calculate_timing_pricing_adjustment(artist_profile, festival_profile)
            },
            'negotiation_leverage': self._assess_negotiation_leverage(artist_profile, festival_profile),
            'market_rate_comparison': self._compare_to_market_rates(artist_profile, festival_profile)
        }
    
    def _calculate_festival_pricing_multiplier(self, festival_profile: FestivalCharacteristics) -> float:
        """Calculate pricing multiplier based on festival characteristics."""
        capacity = festival_profile.basic_info.get('capacity', 0)
        prestige = festival_profile.basic_info.get('prestige_score', 0.5)
        
        # Larger, more prestigious festivals can command higher prices
        capacity_multiplier = 1.0 + (capacity / 200000) * 0.5  # Up to 1.5x for 200K capacity
        prestige_multiplier = 1.0 + prestige * 0.3  # Up to 1.3x for high prestige
        
        return min(2.0, capacity_multiplier * prestige_multiplier)
    
    def _calculate_artist_demand_multiplier(self, artist_profile: Dict) -> float:
        """Calculate pricing multiplier based on artist demand."""
        momentum = artist_profile.get('momentum_score', 0.5)
        streaming_popularity = artist_profile.get('streaming_popularity', 0.5)
        
        demand_score = (momentum + streaming_popularity) / 2
        
        # Higher demand = higher multiplier
        if demand_score > 0.8:
            return 1.5
        elif demand_score > 0.6:
            return 1.2
        elif demand_score > 0.4:
            return 1.0
        else:
            return 0.8
    
    def _calculate_regional_pricing_adjustment(self, artist_profile: Dict, festival_profile: FestivalCharacteristics) -> float:
        """Calculate regional pricing adjustment."""
        regional_appeal = self._assess_regional_appeal(artist_profile, festival_profile)
        
        # Higher regional appeal = higher pricing
        return 1.0 + (regional_appeal - 0.5) * 0.4
    
    def _calculate_timing_pricing_adjustment(self, artist_profile: Dict, festival_profile: FestivalCharacteristics) -> float:
        """Calculate timing-based pricing adjustment."""
        timing_fit = self._assess_timing_fit(artist_profile, festival_profile)
        
        # Better timing fit = higher pricing (artist is available)
        return 1.0 + (timing_fit - 0.5) * 0.2
    
    def _assess_negotiation_leverage(self, artist_profile: Dict, festival_profile: FestivalCharacteristics) -> Dict[str, Any]:
        """Assess negotiation leverage for pricing."""
        artist_demand = artist_profile.get('momentum_score', 0.5)
        festival_prestige = festival_profile.basic_info.get('prestige_score', 0.5)
        
        if artist_demand > 0.8 and festival_prestige > 0.7:
            leverage = 'high_artist_leverage'
        elif artist_demand > 0.7 or festival_prestige > 0.7:
            leverage = 'moderate_leverage'
        else:
            leverage = 'balanced'
        
        return {
            'leverage_type': leverage,
            'artist_position': 'strong' if artist_demand > 0.7 else 'moderate',
            'festival_position': 'strong' if festival_prestige > 0.7 else 'moderate',
            'recommended_strategy': self._generate_negotiation_strategy(leverage)
        }
    
    def _generate_negotiation_strategy(self, leverage: str) -> str:
        """Generate negotiation strategy based on leverage."""
        strategies = {
            'high_artist_leverage': 'Artist has strong leverage - be prepared to negotiate on price and terms',
            'moderate_leverage': 'Balanced leverage - standard negotiation expected',
            'balanced': 'Fair negotiation - focus on mutual value'
        }
        return strategies.get(leverage, 'Standard negotiation approach')
    
    def _compare_to_market_rates(self, artist_profile: Dict, festival_profile: FestivalCharacteristics) -> Dict[str, Any]:
        """Compare recommended pricing to market rates."""
        # Placeholder - would connect to market rate database
        return {
            'below_market': False,
            'at_market': True,
            'above_market': False,
            'market_range': {'min': 15000, 'max': 45000}
        }
    
    def _get_artist_profile(self, artist_id: str) -> Dict[str, Any]:
        """Get complete artist profile."""
        # Placeholder - would connect to artist database
        return {
            'id': artist_id,
            'genres': ['pop', 'electronic'],
            'audience_demographics': {
                'age_range': [18, 35],
                'income_range': [50000, 150000]
            },
            'performance_preferences': {
                'venue_type': 'outdoor',
                'temperature_range': [15, 30]
            },
            'strong_regions': ['North America', 'Europe'],
            'pricing_tier': 'high',
            'festival_history': [],
            'availability_windows': [],
            'stage_requirements': {},
            'momentum_score': 0.7,
            'streaming_popularity': 0.8,
            'base_pricing_range': {'min': 20000, 'max': 60000}
        }
    
    def _analyze_booking_patterns(self, festival_id: str) -> Dict[str, Any]:
        """Analyze historical booking patterns."""
        return {}
    
    def _analyze_audience_preferences(self, festival_id: str) -> Dict[str, Any]:
        """Analyze audience preferences."""
        return {}
    
    def _identify_success_factors(self, festival_id: str) -> List[str]:
        """Identify factors contributing to festival success."""
        return []
    
    def _analyze_genre_performance(self, festival_id: str) -> Dict[str, Any]:
        """Analyze genre performance at festival."""
        return {}
    
    def _compare_demographic_range(self, range1: Optional[List], range2: Optional[List]) -> float:
        """Compare demographic ranges."""
        if not range1 or not range2:
            return 0.5
        
        # Calculate overlap
        overlap_start = max(range1[0], range2[0])
        overlap_end = min(range1[1], range2[1])
        
        if overlap_end < overlap_start:
            return 0.0
        
        overlap_size = overlap_end - overlap_start
        total_size = max(range1[1] - range1[0], range2[1] - range2[0])
        
        return overlap_size / total_size if total_size > 0 else 0.0


class FestivalCharacteristicsDatabase:
    """Database for festival characteristics."""
    
    def get_festival(self, festival_id: str) -> Dict[str, Any]:
        """Get festival characteristics."""
        # Placeholder - would connect to database
        return {}


class ClimateAnalyzer:
    """Analyzer for climate and weather patterns."""
    
    def analyze_historical(self, location: Dict) -> Dict[str, Any]:
        """Analyze historical climate patterns."""
        return {}
    
    def assess_risks(self, location: Dict, dates: List) -> Dict[str, Any]:
        """Assess weather risks."""
        return {}
    
    def seasonal_analysis(self, dates: List) -> Dict[str, Any]:
        """Analyze seasonal considerations."""
        return {}
    
    def identify_optimal_conditions(self, location: Dict, dates: List) -> Dict[str, Any]:
        """Identify optimal conditions."""
        return {}


class RegionalAnalyzer:
    """Analyzer for regional context."""
    
    def music_market(self, location: Dict) -> Dict[str, Any]:
        """Analyze regional music market."""
        return {}
    
    def competitive_landscape(self, location: Dict) -> Dict[str, Any]:
        """Analyze competitive landscape."""
        return {}
    
    def transportation_access(self, location: Dict) -> Dict[str, Any]:
        """Analyze transportation access."""
        return {}
    
    def accommodation_capacity(self, location: Dict) -> Dict[str, Any]:
        """Analyze accommodation capacity."""
        return {}


class DemographicAnalyzer:
    """Analyzer for demographic data."""
    
    def analyze(self, location: Dict) -> Dict[str, Any]:
        """Analyze demographics for location."""
        return {}


class EconomicAnalyzer:
    """Analyzer for economic factors."""
    
    def analyze(self, location: Dict) -> Dict[str, Any]:
        """Analyze economic context."""
        return {}


class GenreAnalyzer:
    """Analyzer for genre data."""
    
    def analyze_genre_fit(self, artist_genres: List, festival_genres: List) -> float:
        """Analyze genre fit."""
        return 0.7
